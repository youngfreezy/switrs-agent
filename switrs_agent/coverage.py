"""Date statements about the selected reconciled snapshot."""
from datetime import date


def bounds(coverage):
    try:
        start = date.fromisoformat(coverage['min_collision_date'])
        end = date.fromisoformat(coverage['max_collision_date'])
        if start > end:
            raise ValueError('Reversed coverage bounds.')
        return start, end
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Valid reconciled coverage metadata is required for calendar-period answers.') from exc


def statement(dataset):
    try:
        start, end = bounds(dataset.get('coverage'))
        return f'This reconciled snapshot covers collision dates {start} to {end}.'
    except ValueError:
        return 'Snapshot date coverage metadata is unavailable.'
