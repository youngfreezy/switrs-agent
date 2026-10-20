"use strict";
// Presentation follows actual stream events, never a timer or a guessed percentage.
function createActivity() {
  const node = id => document.querySelector(id);
  const stages = ['understand', 'query', 'verify'];
  const titles = ['Thinking through your question', 'Querying the records', 'Checking the answer'];
  const tools = {
    get_context: [0, 'Reading dataset coverage and definitions'],
    describe_table: [0, 'Inspecting the available fields'],
    distinct_values: [0, 'Checking recorded values'],
    begin_question: [0, 'Matching the question to supported definitions'],
    prepare_answer: [1, 'Preparing the supported query and its evidence'],
    run_sql: [1, 'Running a read-only query against the records'],
    check_calculation: [2, 'Checking the calculation against query results'],
    submit_answer: [2, 'Verifying the answer, scope and supporting evidence'],
    render_answer: [2, 'Preparing the checked result for display']
  };
  let updates = [];
  function update(stage, message) {
    node('#activity-title').textContent = titles[stage];
    stages.forEach((name, index) => {
      const item = node('#stage-' + name);
      if (index === stage) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    if (updates.at(-1) === message) return;
    updates = [...updates, message].slice(-3);
    node('#activity-log').replaceChildren(...updates.map(text => {
      const item = document.createElement('li'); item.textContent = text; return item;
    }));
  }
  return {
    start(saved = false) {
      updates = []; node('#activity-log').replaceChildren();
      node('#activity').hidden = false;
      node('#activity-stages').hidden = saved;
      update(0, saved ? 'Authenticating this saved result' : 'Connecting to the agent');
      if (saved) node('#activity-title').textContent = 'Checking the saved answer';
      node('#activity-title').focus({preventScroll:true});
      node('#activity').scrollIntoView({block:'nearest', behavior:'instant'});
    },
    event(event) {
      if (event.kind === 'status') update(0, 'Connected · checking dataset availability');
      if (event.kind === 'tool_call') {
        const info = tools[String(event.tool).split('__').at(-1)];
        if (info) update(...info);
      }
      if (event.kind === 'tool_result' && event.result?.ok === false) {
        update(2, 'A check needs another pass · reviewing the evidence');
      }
    },
    finish() { node('#activity').hidden = true; }
  };
}
