"""Static checks on model-written SQL before it reaches the database.

The database connection is read-only on its own. These checks exist to give the model
precise, fixable errors (unknown column, wrong table) and to catch counting mistakes.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

BLOCKED_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "sqlite_scan", "sqlite_attach", "glob", "getenv", "parquet_scan",
}
WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Merge, exp.Copy, exp.Attach, exp.Detach, exp.Set, exp.Pragma, exp.TruncateTable,
)
PERSON_TABLES = {"parties", "victims"}
CRASH_LEVEL_MEASURES = {
    "killed_victims", "injured_victims", "party_count", "severe_injury_count", "other_visible_injury_count",
    "complaint_of_pain_injury_count", "pedestrian_killed_count", "pedestrian_injured_count",
    "bicyclist_killed_count", "bicyclist_injured_count", "motorcyclist_killed_count", "motorcyclist_injured_count",
}


class GuardError(ValueError):
    pass


@dataclass
class GuardReport:
    sql: str
    tables: set[str]
    columns: set[str]
    warnings: list[str] = field(default_factory=list)
    # output column index -> True when the value depends on table data (citable as evidence)
    data_derived: list[bool] = field(default_factory=list)


def check(sql: str, schema: dict[str, dict[str, str]]) -> GuardReport:
    sql = sql.strip().rstrip(";")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL does not parse: {e.errors[0]['description'] if e.errors else e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise GuardError("Send exactly one SELECT statement.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise GuardError("Only SELECT queries (optionally with WITH) are allowed.")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise GuardError(f"{type(node).__name__} is not allowed. Only read queries run here.")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in BLOCKED_FUNCTIONS:
                raise GuardError(f"Function {name} is not allowed.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known_tables = {t.lower() for t in schema}
    tables: set[str] = set()
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.Func) or not t.name:
            raise GuardError("Table functions are not allowed. Query the tables directly.")
        name = t.name.lower()
        if name in cte_names:
            continue
        if name not in known_tables:
            hint = difflib.get_close_matches(name, known_tables, n=1)
            raise GuardError(f"Unknown table '{t.name}'." + (f" Did you mean '{hint[0]}'?" if hint else "")
                             + f" Tables: {', '.join(sorted(known_tables))}.")
        tables.add(name)

    all_columns = {c.lower(): tbl for tbl, cols in schema.items() for c in cols}
    defined_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    for cte in tree.find_all(exp.CTE):
        defined_aliases.update(c.name.lower() for c in cte.find_all(exp.TableAlias) for c in c.columns)
    # Alias names are local to a SELECT; nested scopes may reuse them.
    scopes = {id(scope.expression): scope for scope in traverse_scope(tree)}

    def column_target(col):
        scope = scopes.get(id(col.find_ancestor(exp.Select)))
        while scope is not None:
            source = {a.lower(): value for a, value in scope.sources.items()}.get(col.table.lower())
            if source is not None:
                return source.name.lower() if isinstance(source, exp.Table) else ""
            scope = scope.parent
        return None

    columns: set[str] = set()
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if not name or isinstance(col.this, exp.Star):
            continue
        qualifier = col.table.lower() if col.table else ""
        target = column_target(col) if qualifier else None
        if qualifier and target is None and qualifier not in cte_names:
            raise GuardError(f"Unknown table alias '{col.table}' in '{col.sql()}'.")
        if target:  # qualified by a real table: the column must exist on that table
            table_cols = {c.lower() for c in schema[target]}
            if name not in table_cols:
                hint = difflib.get_close_matches(name, table_cols, n=2)
                where = all_columns.get(name)
                msg = f"Column '{col.name}' is not in table {target}."
                if where:
                    msg += f" It is in {where}."
                elif hint:
                    msg += f" Close matches: {', '.join(hint)}."
                raise GuardError(msg)
            columns.add(f"{target}.{name}")
            continue
        if name in all_columns:
            columns.add(f"{all_columns[name]}.{name}")
            continue
        if name in defined_aliases or qualifier in cte_names or qualifier:
            continue
        hint = difflib.get_close_matches(name, list(all_columns), n=3)
        raise GuardError(f"Unknown column '{col.name}'." + (f" Close matches: {', '.join(hint)}." if hint else "")
                         + " Use describe_table to see real columns.")

    if not tables:
        raise GuardError("The query reads no table. Every number must come from the SWITRS tables.")
    analyzed, flags, sources = _analyze(tree, schema)
    _check_joins(analyzed, sources)
    report = GuardReport(sql=sql, tables=tables, columns=columns, data_derived=flags)
    _lint_fanout(analyzed, report, sources)
    return report


SWITRS = {"collisions", "parties", "victims", "case_ids"}
CHILD_KEYS = {
    frozenset({"collisions", "parties"}): {"case_id"},
    frozenset({"collisions", "victims"}): {"case_id"},
    frozenset({"parties", "victims"}): {"case_id", "party_number"},
    frozenset({"collisions", "case_ids"}): {"case_id"},
    frozenset({"parties", "case_ids"}): {"case_id"},
    frozenset({"victims", "case_ids"}): {"case_id"},
}


# ---- scoped output provenance ------------------------------------------------------------

@dataclass
class _Relation:
    names: list[str]
    derived: list[bool]
    origins: list[tuple[str, str] | None]
    has_rows: bool
    # Only projections/filters of one base table preserve its declared join keys.
    base: str | None = None
    multiplied: list[bool] = field(default_factory=list)
    collision_values: list[bool] = field(default_factory=list)
    fanout_rows: bool = False

    def column(self, name: str) -> tuple[bool, tuple[str, str] | None]:
        matches = [i for i, n in enumerate(self.names) if n == name.lower()]
        if len(matches) != 1:
            return False, None
        i = matches[0]
        return self.derived[i], self.origins[i]

    def involves_collision(self, name: str) -> bool:
        return any(flag for n, flag in zip(self.names, self.collision_values) if n == name.lower())

    def is_multiplied(self, name: str) -> bool:
        return any(flag for n, flag in zip(self.names, self.multiplied) if n == name.lower())


def _analyze(tree: exp.Expression, schema: dict):
    """Qualify a copy for analysis; execute the user's original SQL unchanged.

    SQLGlot resolves lexical scopes and expands stars against the schema. Mixed set-operation
    columns are citable only when every branch is data-derived (conservative per-column policy).
    """
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import Scope, traverse_scope

    try:
        qualified = qualify(tree.copy(), dialect="duckdb", schema=schema)
        scopes = traverse_scope(qualified)
        relations: dict[int, _Relation] = {}
        sources_by_select: dict[int, dict[str, _Relation]] = {}
        by_expression: dict[int, _Relation] = {}
        for scope in scopes:
            node = scope.expression
            if isinstance(node, exp.SetOperation):
                if node.args.get("by_name"):
                    raise GuardError("Set operations BY NAME are not supported for evidence; use matching explicit columns.")
                branches = [relations[id(s)] for s in scope.set_operation_scopes]
                if len(branches) != 2 or len(branches[0].names) != len(branches[1].names):
                    raise GuardError("Set-operation branches must have matching output columns.")
                a, b = branches
                info = _Relation(a.names, [x and y for x, y in zip(a.derived, b.derived)],
                                 [None] * len(a.names), a.has_rows or b.has_rows,
                                 multiplied=[x or y for x, y in zip(a.multiplied, b.multiplied)],
                                 collision_values=[x or y for x, y in zip(a.collision_values, b.collision_values)],
                                 fanout_rows=a.fanout_rows or b.fanout_rows)
            elif isinstance(node, exp.Select):
                sources = {}
                for alias, (_, source) in scope.selected_sources.items():
                    if isinstance(source, exp.Table):
                        name = source.name.lower()
                        names = [c.lower() for c in schema[name]]
                        sources[alias.lower()] = _Relation(names, [True] * len(names),
                                                           [(name, c) for c in names], True, name, [False] * len(names),
                                                           [name == "collisions"] * len(names))
                    elif isinstance(source, Scope) and id(source) in relations:
                        sources[alias.lower()] = relations[id(source)]
                    else:
                        raise GuardError("Cannot establish this query's source columns. Use explicit table projections.")
                sources_by_select[id(node)] = sources
                has_rows = any(s.has_rows for s in sources.values())

                def column_info(col):
                    source = sources.get(col.table.lower())
                    return source.column(col.name) if source else (False, None)

                bases = {source.base for source in sources.values()}
                fanout_rows = (("collisions" in bases and bool(bases & PERSON_TABLES))
                               or any(source.fanout_rows for source in sources.values()))

                def value_nodes(expression):
                    # A scalar's output is one value. Its nested filters are not output lineage.
                    return expression.walk(prune=lambda n: isinstance(n, exp.Subquery))

                names, flags, origins, multiplied, collision_values = [], [], [], [], []
                for projection in node.expressions:
                    expression = projection.unalias()
                    if isinstance(expression, exp.Star) or (isinstance(expression, exp.Column) and expression.is_star):
                        raise GuardError("Cannot expand this wildcard. Select explicit columns.")
                    is_derived = collision_value = duplicate = False
                    for value_node in value_nodes(expression):
                        if isinstance(value_node, exp.Subquery):
                            inner = by_expression.get(id(value_node.this))
                            if inner:
                                is_derived |= any(inner.derived)
                                collision_value |= any(inner.collision_values)
                                duplicate |= any(inner.multiplied)
                        elif isinstance(value_node, exp.Column):
                            source = sources.get(value_node.table.lower())
                            if source:
                                is_derived |= source.column(value_node.name)[0]
                                collision_value |= source.involves_collision(value_node.name)
                                duplicate |= source.is_multiplied(value_node.name)
                        elif isinstance(value_node, exp.Count) and not isinstance(value_node.this, exp.Null):
                            is_derived |= has_rows
                            collision_value |= "collisions" in bases
                    duplicate |= fanout_rows and collision_value
                    # Only an ungrouped, non-window aggregate guarantees a single row.
                    # Windows retain rows; person-level groups can still repeat a crash.
                    if not node.args.get("group") and any(
                            a.find_ancestor(exp.Select) is node and a.find_ancestor(exp.Window) is None
                            for a in expression.find_all(exp.AggFunc)):
                        duplicate = False
                    names.append(projection.alias_or_name.lower())
                    flags.append(is_derived)
                    origins.append(column_info(expression)[1] if isinstance(expression, exp.Column) else None)
                    multiplied.append(duplicate)
                    collision_values.append(collision_value)
                if not node.args.get("group") and any(
                        a.find_ancestor(exp.Select) is node and a.find_ancestor(exp.Window) is None
                        for a in node.find_all(exp.AggFunc)):
                    fanout_rows = False
                row_preserving = (len(sources) == 1 and not node.args.get("joins")
                                  and not node.args.get("group") and not node.args.get("distinct")
                                  and not any(a.find_ancestor(exp.Select) is node for a in node.find_all(exp.AggFunc)))
                base = next(iter(sources.values())).base if row_preserving else None
                info = _Relation(names, flags, origins, has_rows, base, multiplied, collision_values, fanout_rows)
            else:
                raise GuardError("Unsupported query shape for evidence. Use SELECT from the recorded tables.")
            relations[id(scope)] = info
            by_expression[id(node)] = info
        if not scopes:
            raise GuardError("Cannot resolve query output columns.")
        return qualified, relations[id(scopes[-1])].derived, sources_by_select
    except (sqlglot.errors.OptimizeError, KeyError) as e:
        raise GuardError(f"Cannot resolve query columns safely: {e}") from e


# ---- joins ------------------------------------------------------------------------------

def _conjuncts(e: exp.Expression) -> list[exp.Expression]:
    if isinstance(e, exp.And):
        return _conjuncts(e.left) + _conjuncts(e.right)
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return [e]


def _check_joins(tree: exp.Expression, sources_by_select: dict) -> None:
    """Require actual key equalities, including through renamed projection columns."""
    for sel in tree.find_all(exp.Select):
        joins = sel.args.get("joins") or []
        if not joins:
            continue
        sources = sources_by_select[id(sel)]
        if any(source.base not in SWITRS for source in sources.values()):
            raise GuardError("Cannot establish join keys through an aggregated or complex derived relation. "
                             "Use base-table joins or WHERE EXISTS to filter collisions.")
        frm = sel.args.get("from") or sel.args.get("from_")
        left = [(frm.this.alias_or_name.lower(), sources[frm.this.alias_or_name.lower()].base)]

        def key(col):
            relation = sources.get(col.table.lower())
            origin = relation.column(col.name)[1] if relation else None
            return origin[1] if origin and origin[0] == relation.base else None

        for join in joins:
            r_alias = join.this.alias_or_name.lower()
            if r_alias not in sources:
                raise GuardError("This join shape is not supported for evidence. Use WHERE EXISTS or NOT EXISTS instead.")
            r_table = sources[r_alias].base
            on = join.args.get("on")
            if (join.args.get("kind") or "").upper() == "CROSS" or on is None:
                raise GuardError(f"Join to {r_table} has no join condition. Join on the declared keys with =.")
            if on.find(exp.Or):
                raise GuardError(f"Join to {r_table} uses OR. Join conditions must be key equalities joined by AND.")
            links: dict[str, set[str]] = {}
            for part in _conjuncts(on):
                if isinstance(part, (exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE)) and any(
                        key(c) in ("case_id", "party_number") for c in part.find_all(exp.Column)):
                    raise GuardError(f"Join condition '{part.sql()}' compares keys with a non-equality. Use = on the keys.")
                if isinstance(part, exp.EQ):
                    # Qualification expands chained USING keys to COALESCE(left keys).
                    # Each alternative must be a real key from an already joined relation.
                    def key_columns(expression):
                        if isinstance(expression, exp.Column):
                            return [expression]
                        if isinstance(expression, exp.Coalesce):
                            args = [expression.this, *expression.expressions]
                            if all(isinstance(c, exp.Column) for c in args):
                                return args
                        return []
                    aa, bb = key_columns(part.left), key_columns(part.right)
                    if not aa or not bb:
                        continue
                    left_aliases = {alias for alias, _ in left}
                    sides = ({c.table.lower() for c in aa}, {c.table.lower() for c in bb})
                    if not ((sides[0] <= left_aliases and sides[1] == {r_alias} and len(bb) == 1)
                            or (sides[1] <= left_aliases and sides[0] == {r_alias} and len(aa) == 1)):
                        continue
                    key_names = {key(c) for c in aa + bb}
                    if None in key_names or len(key_names) != 1:
                        continue
                    for a in aa:
                        for b in bb:
                            qa, qb = a.table.lower(), b.table.lower()
                            other = qb if qa == r_alias else qa if qb == r_alias else None
                            if other in {alias for alias, _ in left}:
                                links.setdefault(other, set()).add(key(a))
            if not links:
                raise GuardError(f"Join to {r_table} has no key equality with a table already in the query.")
            ok = False
            for alias, table in left:
                keys = links.get(alias, set())
                need = CHILD_KEYS.get(frozenset({table, r_table}))
                if need and need <= keys:
                    ok = True
                if r_table in PERSON_TABLES and table in PERSON_TABLES and "party_number" in keys:
                    if any("case_id" in links.get(a2, set()) for a2, _ in left):
                        ok = True
            # A person-to-person join must use party_number even when case_id also links to collisions.
            if r_table in PERSON_TABLES and any(t in PERSON_TABLES for _, t in left):
                if not any("party_number" in links.get(a, set()) for a, t in left if t in PERSON_TABLES):
                    raise GuardError("victims joins parties on case_id AND party_number. Joining on case_id alone "
                                     "matches each victim to every party in the crash.")
            if not ok:
                raise GuardError(f"Join to {r_table} does not use its full declared key.")
            left.append((r_alias, r_table))


# ---- fan-out ----------------------------------------------------------------------------

def _lint_fanout(tree: exp.Expression, report: GuardReport, sources_by_select: dict) -> None:
    """Reject sums of multiplied crash values, including through derived projections.
    COUNT without DISTINCT over a direct person join is a warning; EXISTS stays in its own scope."""
    for sel in tree.find_all(exp.Select):
        scope = {alias: source.base for alias, source in sources_by_select[id(sel)].items()}
        tables = set(scope.values())
        direct_fanout = "collisions" in tables and bool(tables & PERSON_TABLES)
        for agg in sel.find_all(exp.Sum, exp.Avg):
            if agg.find_ancestor(exp.Select) is not sel:
                continue
            for col in agg.find_all(exp.Column):
                owner = scope.get((col.table or "").lower()) if col.table else None
                source = sources_by_select[id(sel)].get((col.table or "").lower())
                inherited_fanout = source and source.is_multiplied(col.name)
                if inherited_fanout or (direct_fanout and (owner == "collisions" or
                                                          (owner is None and col.name.lower() in CRASH_LEVEL_MEASURES))):
                    raise GuardError(
                        f"{agg.sql()} sums a crash-level column after joining to parties/victims, "
                        "so each crash is counted once per person. Filter people with WHERE EXISTS (...) instead. "
                        "SUM(DISTINCT ...) is not a fix.")
        for count in sel.find_all(exp.Count):
            inherited_rows = any(source.fanout_rows for source in sources_by_select[id(sel)].values())
            if (direct_fanout or inherited_rows) and count.find_ancestor(exp.Select) is sel and not isinstance(count.this, exp.Distinct):
                report.warnings.append(
                    "fan-out: this query joins collisions to parties/victims and uses COUNT without DISTINCT. "
                    "That counts people, not crashes. To count crashes with at least one matching person, use "
                    "WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id = c.case_id AND ...).")
                break
