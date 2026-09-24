# Dashboard

Studio opens on Dashboard. Its overview spans all open projects, so work running in another
workspace is still visible. It refreshes every five seconds while visible; an offline label
and the last successful update time distinguish old information from a live snapshot.

## Read the overview

- **Working now** shows actual running, waiting or stopping agent processes, their model and
  the current mission phase. View activity opens the run; Live map opens its dependencies.
- **Needs your attention** links to blockers, missing checks, plans and results waiting for
  acceptance. Open approvals shows the existing Yes / No / custom-response inbox.
- **Task board** lists company assignments and project executions, with open, all and completed
  filters. Disabled rules and paused Drivers are labeled. Completed standalone runs are execution
  history, not independently accepted products. Standalone history uses the latest 100 server runs,
  with up to 20 completed/failed entries shown in the board.
- **Companies** shows whether each Driver is active or paused and links to its management view.

## Give an assignment

1. Select the working project.
2. Choose a company associated with that project, or Standalone task.
3. Assign a company department or a standalone model.
4. Describe the task and the observable completion conditions in Done when.
5. Click Add to queue or Start task.

Company work inherits the company's worker, reviewer, permissions and limits. A paused company
stores new tasks without starting them; use Manage company to start its Driver. New dashboard
assignments are one-time tasks. Use the company view for dependencies or recurring work.

Standalone work starts immediately with normal tool approvals and does not receive an independent
review. More options exposes single-agent/team mode and a step limit. For company work, it exposes
priority and independent verification commands. Both support explicit constraints.

Polling never replaces the task brief or acceptance fields. Failed submissions preserve the draft;
a successful save clears it and confirms whether the task was queued or started. Do not resend a
task if the server connection fails after submission until you have checked the board.

Workspace keeps the file editor and detailed run view. 3D Office and Live map remain accessible in
the header; Models, Products, AI Projects, Templates, Preview and Decision lab are under More.
