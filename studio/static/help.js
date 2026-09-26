"use strict";
// Contextual help is local UI only: it never submits a form or calls an API.
const studioHelpRules = [
  [/sign in and resume|owner access key|signed out/i, "Signs you in to Studio again after your session expired.", "Enter the owner access key stored on the Studio server. The key is not stored in the browser. Drafts on this page are kept and updates resume after sign-in.", "Sign in again after the Studio server restarted."],
  [/skip and redirect/i, "Declines only this tool action and sends your instruction to the agent; the run continues.", "Write what the agent should do instead, then choose Skip and redirect. The declined action does not run.", "Do not restart the service; read its status and report it instead."],
  [/reject and stop/i, "Declines this tool action and stops the current run.", "Use when the run must not continue. The controller may retry the task automatically within its limits; use Skip and redirect to keep the run going with new instructions.", "Stop a run that tries to modify production files during a read-only audit."],
  [/retry now|answer and retry/i, "Resumes blocked work from its saved state within the remaining limits.", "Read the stated cause first. Answering a recovery question from the Driver or the controller resumes the work; to revise the brief, change the limits or end the work, open the details before you answer.", "Retry after the model server is available again."],
  [/save answer only/i, "Records your answer to the agent's question without resuming the blocked work.", "Use when you want to decide later. The work stays blocked until you choose Retry now in the inbox or resume it from the execution details.", "Record the staging host now and retry after the maintenance window."],
  [/requeue/i, "Queues a cancelled or expired company task again under the current company limits.", "Check why it stopped before requeueing. The Driver starts it when it is eligible; runs already used remain counted.", "Requeue an audit that expired while the model server was offline."],
  [/delivery process|process scope/i, "Scales work and context budgets to task impact while retaining separate review.", "Use Auto normally. Light reduces work and reading budgets. Security, database and infrastructure scope or changed sensitive paths force Sensitive.", "A README typo can use Light; an authentication change cannot."],
  [/brief readiness|revise (?:task )?brief|save revised brief/i, "Checks whether the requested scope and completion criteria agree before execution.", "Resolve conflicting instructions in the brief editor and explain the revision. The previous brief is preserved. Network access is tested by the worker, not assumed from planning restrictions.", "For a read-only audit, allow documented unknown integrations instead of requiring repairs."],
  [/result card|refresh result evidence/i, "Connects outputs to actual checks, review and an immutable file snapshot.", "Refresh to compare the current files with recorded evidence. A changed source, permission or recorded Git revision makes evidence stale. Notes updated after acceptance are labeled separately.", "Exit zero with zero discovered tests does not pass a test check."],
  [/continuation handoff|retry notes/i, "Preserves a compact continuation record and accepted-delivery notes.", "The handoff identifies completed work, remaining work and file hashes. Notes use conflict-checked updates; a concurrent edit is kept for manual resolution.", "After restart, read a changed reference again and continue the selected pending task."],
  [/dashboard|working now|task board/i, "Shows actual runs, queued work and requests across all projects.", "Open activity to inspect a running agent, or review a blocker. Refresh updates status without discarding your task draft.", "See the worker's task and the reviewer's current phase before assigning more work."],
  [/task destination|assign to/i, "Chooses who receives the new assignment.", "A company task goes to its selected department and uses its worker, reviewer and policy. A standalone task uses the selected model immediately.", "Choose your company and Delivery to queue a reviewed implementation task."],
  [/done when/i, "Defines the observable result that completes this task.", "Write one acceptance condition per line. Company tasks pass these conditions to the reviewer; standalone tasks include them in the brief.", "report.md exists and links each finding to an inspected source."],
  [/add to queue|start task/i, "Saves a company assignment or starts a standalone run.", "Choose the project and responsible department or model, then describe the task and completion criteria. A paused company keeps its task queued until you start its Driver.", "Queue a landing page update for Delivery and follow it in Working now."],
  [/browser pilot|fixture|observe and run/i, "Tests a closed browser-action loop on a local synthetic page.", "Each step observes the fixture, validates an allowed action, rejects changed state and uses the action once. Finish requires an independent DOM check. Optional model proposals use the selected lab provider.", "Run three steps to enter blue, filter the list, and verify that only Blue desk is visible."],
  [/decision lab|evaluation type|evaluate without/i, "Runs a saved, read-only experiment using a small versioned rubric.", "Choose idea, document or CRM request, paste supporting evidence and select a provider. Evaluate sends one bounded request. Inspect unknowns before saving a Markdown report.", "Score a product idea with customer observations; missing demand evidence produces Validate rather than launch approval."],
  [/evaluation provider/i, "Selects the model that receives the evidence pasted into Decision lab.", "Use a local chat-compatible model, or optional TypeSafe cloud with TYPESAFE_API_KEY configured on the server. Requests start only when you click Evaluate.", "Choose your local Qwen for a short, redacted CRM routing experiment."],
  [/evidence text|claim or question/i, "Defines the exact evidence and claim used by the evaluation.", "Provide a short source excerpt or brief, with observed facts and known unknowns. Inputs are limited to 12,000 bytes and are not silently truncated. Extract text from scans first.", "Claim: the integration works. Evidence: a timestamped successful test and its limitations."],
  [/decision mode|shadow.*compare/i, "Controls whether a model may propose task priority.", "Off sends no request. Shadow records a comparison and continues in plan order. Select may pick only an already eligible task; errors use plan order.", "Try Shadow on a small project before enabling Select."],
  [/decision provider|select next task/i, "Chooses an optional provider for bounded decisions.", "Local profiles use their configured endpoint. TypeSafe is cloud-based and sends compact goal and task summaries; configure TYPESAFE_API_KEY on the server. Choosing no provider keeps plan order.", "Select a local model and use Shadow to compare its choices."],
  [/review evidence/i, "Shows the exact saved inputs supplied to the reviewer.", "Inspect the snapshot, file hashes, packet size and read limits. A truncated excerpt is incomplete evidence. Changes to a source invalidate approval.", "Check whether a missing test result caused an insufficient_evidence verdict."],
  [/3d office|agent office|office workspace/i, "Shows saved work as a three-dimensional office with department desks.", "Choose a company, select a desk or use the list to inspect real status and open its run. Empty desks are illustrative, and do not create workers.", "Select a blocked desk to open the exact task and its recorded blocker."],
  [/rotate office|reset camera|zoom office/i, "Changes your view of the 3D office.", "Use the rotation and zoom buttons, or drag the floor. Reset camera restores the default angle. This never starts or stops work.", "Rotate left to inspect the Delivery desks, then reset the camera."],
  [/list view|show 3d office|all departments/i, "Switches or filters the office's accessible task overview.", "Use the list to inspect every task, including tasks beyond the four visible seats in a department.", "Choose a task in the list and open its live map."],
  [/begin a new time horizon|restart.*horizon/i, "Starts a new scheduling time window the next time the Driver starts.", "Enable only when you want a new deadline. Used runs and executions remain counted; this does not reset the budget.", "Extend the pilot into a new week while keeping its existing usage history."],
  [/decision.*model|model.*decision/i, "Optionally asks a model to choose the next task among tasks already eligible to run.", "Leave this unset for the deterministic queue. A timeout or invalid choice falls back to the queue; the model cannot bypass dependencies or grant permissions.", "Choose between two ready implementation tasks after their prerequisites pass."],
  [/auto.*refresh|refresh.*auto/i, "Refreshes the web preview after saved project files change.", "Enable to see saved edits without manually refreshing. Unsaved editor content is not included.", "Save a CSS change and see the preview update."],
  [/authentication|^auth$/i, "Selects how Studio authenticates requests to this model endpoint.", "Use no authentication only when your endpoint permits it. For an API key, select the environment-variable method and configure that variable on the host.", "Use environment authentication with INTERNAL_API_KEY for your protected endpoint."],
  [/file path|new.*file/i, "Creates a text file at a relative path inside the selected project.", "Enter a project-relative filename. Existing files and protected paths cannot be silently overwritten through this action.", "src/example.py"],
  [/^files$|explorer/i, "Lists files in the selected project that are available to the editor.", "Expand a folder and select a file to read or edit it. Save changes before switching files.", "Open README.md and update the setup instructions."],
  [/outputs|artifacts/i, "Lists output files recorded for the selected agent run.", "Open or download the relevant artifact and inspect its contents. A listed file alone does not prove the task met its criteria.", "Open the generated report and verify its sources."],
  [/getting started|how to start/i, "Opens the quick-start guide to Studio.", "Start with a project folder, configure a model and give it one bounded task.", "Ask the agent to inspect the project without changing files."],
  [/auto.*accept|accept.*automatic/i, "Lets the controller accept a completed result after its independent checks pass.", "Enable only when you trust the configured verification commands. It does not enable tool approval.", "A documentation update is accepted after its required file and content checks pass."],
  [/auto.*approv|allow.*automatic|allow tools without individual approval|automatic.*tool/i, "Allows the agent to use its permitted tools without asking about each action.", "Enable for a trusted workspace. Hard tool restrictions and project limits still apply. Result acceptance is separate.", "Allow file edits and test runs in a disposable development project."],
  [/manual.*accept|accept.*manual|without.*automatic.*check/i, "Records your manual acceptance without claiming that automated verification passed.", "Inspect the actual output first, then explicitly acknowledge the missing automated checks.", "Read a short report yourself before accepting it manually."],
  [/verification.*command|check.*command|command.*check|independent.*check/i, "Defines the commands that independently verify the product.", "Enter one command per line. Commands run in the working copy; shell operators are not automatically interpreted. Only approve commands you intend to run.", "python -m unittest discover\nnpm test"],
  [/review.*model|model.*review|reviewer/i, "Selects the model that reviews architecture and functional evidence in a separate session.", "The worker implements the change. The reviewer assesses design and behavior rather than auditing every line. Approved automatic checks run before the final review; passing them proves only the tested scenarios.", "Use AMD for coding and Intel for architecture review, then review the results of functional tests."],
  [/worker.*model|model.*work|model.*execution|worker and planner|worker and scheduler/i, "Selects the model used to plan and carry out the work.", "Choose a configured, reachable model with tool support. Changing the selection does not change an already running attempt.", "Select your local Ollama coding profile."],
  [/context_window|context window|context length/i, "Sets the context capacity advertised to the agent for this model.", "Match the capacity actually configured on your model server. This is not a setting that increases server memory.", "32768 for a server configured for a 32K-token context."],
  [/max_output_tokens|output.*token|token.*output/i, "Caps the model's requested response length.", "Use a limit supported by the model and leave room for the prompt inside its context window.", "4096 tokens for one response."],
  [/reasoning|thinking effort/i, "Controls reasoning settings for providers that support them.", "Choose a supported level. More reasoning can increase latency and usage; unsupported models may ignore the setting.", "Use high for a difficult code review, if the selected model supports it."],
  [/base_url|api url|api address|endpoint/i, "Sets the model service address reachable from the Studio server.", "Enter the provider's compatible API base URL. Localhost refers to the machine running Studio.", "http://localhost:11434/v1 for Ollama on the Studio host."],
  [/api_key_env|environment.*key|key.*environment|credential.*variable/i, "Names the environment variable containing the provider API key.", "Enter the variable name, not the secret value. Configure the secret on the Studio host.", "OPENAI_API_KEY"],
  [/protocol/i, "Selects the API format spoken by the model provider.", "Match the provider's documented interface. The server URL alone does not select a compatible protocol.", "Chat Completions for an OpenAI-compatible local endpoint."],
  [/dialect|ollama/i, "Selects provider-specific request handling.", "Use the Ollama dialect for an Ollama endpoint; keep the default for a standard compatible API.", "An Ollama server at port 11434 with its installed model name."],
  [/model.*name|name.*model/i, "Identifies the exact model to request from the server.", "Copy an available model identifier from your provider or the connection test. A display name is not a model identifier.", "Use the exact name returned by your Ollama model list."],
  [/profile.*id|profile identifier/i, "Gives this model configuration a stable local identifier.", "Use a short unique name. Other project settings refer to this profile.", "local-coder"],
  [/test.*connection|check.*connection|verify.*api/i, "Checks whether the configured model endpoint responds.", "Run after entering the URL and provider settings. A successful connection is not a completed model task.", "Confirm that the endpoint lists the model you plan to use."],
  [/log ?in|sign ?in|connect.*account|authorization|oauth/i, "Connects an optional provider account through its supported local CLI.", "Complete the provider's sign-in flow in a browser on the Studio host. Never paste account tokens into a task.", "Use an existing supported account, or continue with a local model without signing in."],
  [/remove.*studio|remove.*profile/i, "Removes this model profile from Studio.", "Stop runs using the profile first. This does not delete the model weights or sign other applications out.", "Remove a temporary test endpoint after switching to another profile."],
  [/attempt_minutes|minutes.*run|minutes.*attempt|run.*minutes/i, "Limits the active duration of each worker attempt.", "Allow enough time for your model and tools. This is separate from the whole project's deadline.", "20 minutes per attempt for a local model."],
  [/max_turns|steps.*run|steps.*attempt|step limit|turn limit|maximum.*steps|limit steps/i, "Limits the number of agent turns in one attempt.", "Increase for a larger task only when needed. More turns do not guarantee a better result.", "40 steps for a bounded implementation and test cycle."],
  [/run budget|runs.*execution|total.*runs|reserve.*runs|additional.*runs|attempt.*budget|run.*reservation/i, "Limits or reserves worker attempts for the selected project or company.", "Set a bounded total. A company reservation uses its remaining budget; this is not a currency or provider-billing cap.", "Reserve 20 attempts from a company's 100-attempt budget."],
  [/horizon|limit.*days|days.*limit|days.*execution/i, "Sets how long the controller is allowed to keep scheduling work.", "Choose a bounded number of days. Studio and its host must remain running; this does not guarantee uninterrupted autonomy.", "7 days for a supervised pilot."],
  [/maintenance.*interval|interval.*maintenance|interval.*hour|every.*hour|recurr/i, "Schedules repeat work at a configured interval.", "Set an interval and a maximum number of executions. Missed intervals do not create an unlimited catch-up queue.", "Review the project once every 24 hours, for at most 7 executions."],
  [/cycle limit|execution limit|total executions|number of executions|maximum.*executions/i, "Caps the number of project executions created by this controller.", "Keep the limit small for a new workflow. It is separate from the steps and attempts inside one execution.", "3 executions: initial work, one revision and one maintenance pass."],
  [/priority/i, "Helps the controller choose among eligible work items.", "Set priority together with dependencies and a clear scope. A high priority cannot bypass a blocked dependency.", "Prioritize a confirmed production defect over a cosmetic improvement."],
  [/dependenc/i, "Specifies work that must finish before this item can start.", "Select only genuine prerequisites and avoid cycles. Independent work can remain eligible while another item waits.", "Complete the server inventory before writing the integration plan."],
  [/department/i, "Assigns the work to an organizational role or department context.", "Choose the department responsible for the outcome. This is not a separate security account or an extra worker slot.", "Assign a code review to Delivery and an infrastructure inventory to Platform."],
  [/company.*name|name.*company/i, "Names the company workspace shown in the Driver.", "Use a recognizable name; add its projects and goals separately.", "Example Studio"],
  [/goal|purpose|desired result|measurable.*result/i, "Describes the outcome the agent should deliver.", "State the audience, scope and concrete deliverables. Put sensitive credentials in a secure configuration, not in this field.", "Create a landing page with a pricing section and save it as index.html."],
  [/criteria|acceptance/i, "Defines observable conditions the result must satisfy.", "Use one clear requirement per line and pair testable requirements with verification commands.", "The file exists. The form validates its inputs. The tests pass."],
  [/constraints|out of scope|restrictions/i, "Defines boundaries the agent must respect.", "State what must not change and which actions need a separate decision. Instructions do not replace OS-level isolation.", "Read the server only; do not restart services or publish changes."],
  [/context and links|sources|reference|background/i, "Supplies documents and links relevant to the task.", "Use accessible sources and explain why they matter. Do not include passwords or tokens.", "Link the public API documentation and name the local requirements.md file."],
  [/brief|task for.*agent|task description|instructions/i, "Tells the agent what to do in the selected workspace.", "Specify the requested files, constraints and a way to check success. Save editor changes before starting.", "Add a contact section to index.html and verify its links."],
  [/reason.*change|change.*reason/i, "Records why the task brief or criteria are being revised.", "Describe the discovered contradiction or new evidence. The original wording remains in the revision history.", "The inventory may report an integration as unverified when the search scope is documented."],
  [/approve.*plan|confirm.*plan/i, "Authorizes execution of the proposed project plan.", "Read the tasks, dependencies and open questions first. Confirm only when they match the intended scope.", "Approve an inventory plan that is limited to read-only inspection."],
  [/revise|revised.*brief|save.*corrected|save.*revised/i, "Saves a documented correction to an unfinished task.", "Pause the project and its parent Driver first, edit the brief or criteria, and explain the change. Resume separately.", "Replace an impossible requirement with an evidence-backed inventory criterion."],
  [/accept.*product|accept.*result|take.*over.*product/i, "Accepts the verified output and applies eligible changes to the original project.", "Inspect the files and verification evidence first. Conflicting newer changes prevent automatic merging.", "Accept the reviewed landing page after its configured checks pass."],
  [/recheck|verify.*again|check.*again/i, "Runs the result through verification again.", "Use after correcting the product or when the recorded verification is no longer current.", "Recheck after a test failure has been fixed."],
  [/start.*driver|enable.*driver/i, "Starts scheduling eligible company work within its limits.", "Configure projects, tasks, models and budgets first. Open questions and missing checks can still require your input.", "Start a Driver with one read-only inventory task and a fixed run budget."],
  [/pause.*driver|pause.*product|^pause$/i, "Pauses the selected controller or project.", "Use before changing its settings or when you want to stop further work. Existing deployed services have their own stop control.", "Pause the Driver while reviewing a blocked task."],
  [/resume|continue/i, "Continues a paused project or controller from its saved state.", "Resolve the stated blocker and check remaining limits first. Already recorded work is retained.", "Resume after supplying a missing repository URL."],
  [/cancel.*project|end.*project|terminate.*project/i, "Ends this project instead of temporarily pausing it.", "Use when you no longer want it to continue. Read its saved results before ending it.", "End an abandoned experiment and keep its recorded history."],
  [/^stop$|stop.*run/i, "Stops the active agent run and cleans up its child processes.", "Use when the task is no longer useful or is outside its intended scope. Wait for the stopped state before starting another run.", "Stop a test run that is repeatedly trying the wrong command."],
  [/^yes$|^approve$|^allow$/i, "Allows the specific pending tool action shown on this card.", "Read the requested action and its risks first. A required confirmation must match the displayed instruction.", "Approve a proposed edit to the intended project file."],
  [/^no$|^deny$|^reject$/i, "Declines the specific pending action.", "Use when the action is incorrect or outside the task scope. A custom reply can explain a safer next step.", "Reject a request to modify a production file during a read-only inventory."],
  [/send.*reply|send.*answer|custom.*reply|your.*answer|response|feedback/i, "Provides an answer or correction for the pending question or action.", "Give the missing information or explain the intended alternative. A custom tool reply declines the current action and supplies feedback.", "Use the staging project and only read its configuration."],
  [/live.*map/i, "Shows the project's real task dependencies, active step and recorded blockers.", "Select an execution and click a node for details. Activity alone does not prove that a task is complete.", "See whether the worker is editing a file, waiting for input or handing work to review."],
  [/find.*current|current.*step/i, "Moves the diagram to the controller's current step.", "Use after panning around a larger plan. Open that node to inspect its details.", "Jump back to the active review node."],
  [/log|activity|console|parameters|history/i, "Shows recorded work, tool calls or execution details.", "Inspect the relevant run and its latest events. Read errors together with the current controller state.", "Open the failed test command and inspect its exit code and log."],
  [/preview.*entry|entry.*file|html.*file/i, "Selects the HTML entry point for the local preview.", "Use a saved file inside the selected project. The preview does not build a framework application for you.", "index.html or dist/index.html after a successful build."],
  [/start.*preview|preview.*start/i, "Starts a local static preview of the selected HTML file.", "Save the files and choose the entry point first. This is a preview, not public hosting.", "Preview a saved landing page at index.html."],
  [/stop.*preview/i, "Stops the local preview server.", "Use before previewing another project. Your project files remain on disk.", "Stop this preview, then switch to a different project."],
  [/starter.*web|starter.*site|create.*website/i, "Creates a small starter website in the chosen project folder.", "Choose a new folder and then edit the generated HTML, CSS and JavaScript.", "Create web-preview/index.html and replace its example content."],
  [/preview|mobile.*390|viewport/i, "Shows a clickable preview of saved web files.", "Select a saved entry point, start the preview and choose desktop or mobile width. The host browser must be able to reach the preview port.", "Check the landing page at a 390-pixel mobile width."],
  [/service.*command|start.*command|launch.*command/i, "Defines how to start the optional local product service.", "Use a command supported by the host. Available placeholders include {host}, {port} and {data_dir}; dependencies and data migrations remain your responsibility.", "python3 -m http.server {port} --bind {host}"],
  [/health.*url|health.*path|http.*path/i, "Selects the endpoint used to check a local service's availability.", "Use a lightweight route that should respond successfully when the service is ready.", "/health"],
  [/expected.*text|response.*text/i, "Adds a response-content condition to the HTTP health check.", "Enter a short stable marker returned by the healthy service, or leave it empty when the status code is sufficient.", "ok"],
  [/stop.*service/i, "Stops the selected locally managed service.", "Use the service control, not the Driver pause button. Stopping also cancels pending automatic deployment for that service.", "Stop a temporary preview API when the pilot is finished."],
  [/deploy|deployment/i, "Manages a verified version as a local service.", "Configure its start command and health check. A candidate replaces the old service only after the required health checks pass.", "Start a checked static site on a local port before replacing the previous version."],
  [/restore|rollback|backup/i, "Restores recorded source files or prepares a previous verified version.", "Inspect the change preview and conflicts before applying it. Product databases, secrets and installed dependencies need separate backups.", "Restore the previous accepted HTML version while keeping a backup of the current files."],
  [/download.*report|export.*report/i, "Downloads a Markdown snapshot of the selected company's recorded work.", "Use it to review the portfolio and decisions outside Studio. It does not perform the pending external actions.", "Save a weekly status report for your own review."],
  [/mark.*performed|record.*performed|external.*action|evidence.*performed/i, "Records an external action performed by a person.", "Describe what actually happened and attach an appropriate reference. The button itself does not send messages, pay or deploy.", "Record that you manually published an approved update, with its URL."],
  [/single.*agent/i, "Uses one stateful agent to handle the task.", "Choose this for focused edits, inspections or document work.", "Ask one agent to update a README and check its links."],
  [/agent.*team|team.*agent/i, "Uses a coordinator that can delegate bounded work to sub-agents.", "Choose an eligible Frontier model profile and a task that benefits from decomposition. This is distinct from the Company Driver's single worker slot.", "Have a team investigate independent parts of a repository."],
  [/template/i, "Provides reusable role instructions and task context.", "Review the template and save a project copy before using it. Loading a role template does not start all of its roles as workers.", "Use AI Build Company as a starting point and adapt its goals and limits."],
  [/company|builder|driver/i, "Organizes projects, departments and recurring work in a saved company workspace.", "Create the company, add projects and bounded tasks, then start the Driver when ready.", "Manage a website and an internal tool under one company."],
  [/product/i, "Groups a digital product's work requests, accepted versions and maintenance.", "Create a product with a goal and lasting criteria, then add a scoped change or start its next execution.", "Maintain a website through an initial version and later bug fixes."],
  [/project|folder|directory/i, "Selects the project files or workspace the agent will use.", "Choose the intended folder on the Studio host. Save open edits before switching projects.", "/home/user/projects/my-project"],
  [/model/i, "Selects or configures the model used by Studio.", "Use a reachable endpoint and an installed model. Switching this selection does not alter a run already in progress.", "Choose a local model profile for the next task."],
  [/save/i, "Saves the current edit or form values.", "Review your changes first. A conflict means the source changed elsewhere; reload and reconcile instead of overwriting blindly.", "Save your HTML edit before refreshing its preview."],
  [/refresh|reload/i, "Reloads the selected view from saved state.", "Save or intentionally discard local edits first. Refreshing a view is not the same as starting a new agent run.", "Refresh the file list after an agent creates a new file."],
  [/^run|^start|execute/i, "Starts the selected task or execution with its saved settings.", "Confirm the project, model, task brief and limits before starting. Follow the run in Activity or the Live map.", "Run a bounded task to create and verify a small README update."],
  [/open|view|show/i, "Opens the selected item or its details.", "Inspect the context and recorded state before taking a follow-up action.", "Open a completed run to read its report and saved output files."],
  [/add|create|new/i, "Creates an item using the values in the current form.", "Provide a descriptive name, scope and required settings. Creation alone does not prove that work has completed.", "Add one small, testable task before starting a larger work queue."],
  [/title|name/i, "Gives the item a recognizable display name.", "Use a short title that describes its purpose. Keep secrets out of names.", "Landing page refresh"],
  [/type|kind/i, "Classifies the requested work or product.", "Choose the closest category; it supplies context rather than installing a complete technology stack.", "Choose Websites and apps for a landing page."],
  [/close|dismiss|^×$/i, "Closes this panel without stopping the Studio server.", "Save form edits first if you want to keep them. Use the separate stop control to stop running work.", "Close the Live map and reopen it later to inspect progress."],
];
function studioHelpText(node) {
  const label = node.labels?.[0] || (node.matches?.("label") ? node : null);
  if (label) return Array.from(label.childNodes).filter(n => n.nodeType === 3 || (n.nodeType === 1 && !n.matches("input,select,textarea,.context-help-trigger"))).map(n => n.textContent).join(" ").replace(/\s+/g," ").trim();
  return node.getAttribute?.("aria-label") || node.getAttribute?.("title") || node.textContent?.replace(/\s+/g," ").trim() || node.getAttribute?.("placeholder") || node.name || node.id || "Control";
}
function studioHelpFor(node) {
  const label = studioHelpText(node), key = [node.id || "", node.name || "", label].join(" ").replaceAll("_"," ").replaceAll("-"," ");
  if(node.classList?.contains("tree-row"))return {title:label,purpose:node.classList.contains("directory")?"Expands a folder in the current project.":"Opens this saved project file in the editor.",how:"Select the intended project and save any existing edits before opening another file.",example:node.classList.contains("directory")?"Expand src to inspect its source files.":"Open README.md, edit it and use Save to write the change to disk."};
  const semanticFields = {auth:"Authentication",context_window:"Context window",max_output_tokens:"Output tokens",chat_dialect:"Dialect",id:"Profile identifier",model:"Model name",api_key_env:"API key environment variable",base_url:"API URL", "preview-auto":"Auto refresh", "preview-folder":"Starter website", "preview-size":"Preview viewport", "auto-approve":"Auto approve tools", "refresh-files":"Refresh files", "turn-limit":"Step limit", "task-input":"Task description", "flow-mission":"Live map", "probe-model":"Test connection"};
  const hint = semanticFields[node.id] || semanticFields[node.name];
  const specific = hint && studioHelpRules.find(([pattern]) => pattern.test(hint));
  if (specific) return {title:label,purpose:specific[1],how:specific[2],example:specific[3]};
  const match = studioHelpRules.find(([pattern]) => pattern.test(label)) || studioHelpRules.find(([pattern]) => pattern.test(key.trim()));
  if (match) return {title:label, purpose:match[1], how:match[2], example:match[3]};
  const form = node.closest?.("form"), input = node.matches?.("input,textarea,select");
  if (input) return {title:label,purpose:"Supplies this setting to the current form.",how:"Enter the value requested by the label, then use the form's save or create action. Do not enter secrets unless the field explicitly supports secure credentials.",example:node.placeholder || (node.type === "number" ? "Start with the displayed limit and increase it only when needed." : "Use a short, specific value for this project.")};
  return {title:label,purpose:form ? "Applies the selected action to this form's item." : "Opens or controls the selected workspace item.",how:"Read the item's current state and save any changes before taking the next action.",example:"Inspect the selected item and its saved details before continuing."};
}
// Help icons are pointer shortcuts only (hidden from assistive technology and outside the Tab order).
// Keyboard users press F1, or ? on a non-text control, to open help for the focused control.
function studioHelpKey(event, active) {
  if (event.key === "F1") return true;
  if (event.key !== "?" || event.ctrlKey || event.metaKey || event.altKey || !active) return false;
  return !active.isContentEditable && !active.matches?.("textarea,select,input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit])");
}
function initStudioHelp() {
  const attached = new WeakSet(), triggers = new WeakMap(), pending = new Set();
  let current = null, returnTo = null, popup = null, scheduled = false;
  const selector = "input,select,textarea,button,summary";
  function close(returnFocus=false) {
    const prior=current, target=returnTo;current=null;returnTo=null;
    if(prior?.classList.contains("context-help-trigger"))prior.setAttribute("aria-expanded","false");
    if(popup){if(popup.matches(":popover-open"))popup.hidePopover();popup.hidden=true;}
    if(returnFocus && target?.isConnected)target.focus();
  }
  function place() {
    // Follow the anchor while its container scrolls; close only when it leaves the viewport.
    if(!current||!popup||popup.hidden)return;
    if(!current.isConnected){close();return;}
    const rect=current.getBoundingClientRect();
    if(rect.bottom<0||rect.top>innerHeight){close();return;}
    const box=popup.getBoundingClientRect(),gap=10;
    popup.style.left=Math.max(gap,Math.min(rect.left,innerWidth-box.width-gap))+"px";
    popup.style.top=Math.max(gap,rect.bottom+gap+box.height<=innerHeight?rect.bottom+gap:rect.top-box.height-gap)+"px";
  }
  function show(anchor,target,focus=false) {
    if(!anchor.isConnected)return;
    if(current && current!==anchor && current.classList.contains("context-help-trigger"))current.setAttribute("aria-expanded","false");
    const info=studioHelpFor(target);current=anchor;returnTo=target;
    if(anchor.classList.contains("context-help-trigger"))anchor.setAttribute("aria-expanded","true");
    if(!popup){popup=document.createElement("section");popup.id="studio-context-help";popup.className="context-help-popup";popup.setAttribute("popover","manual");popup.setAttribute("role","dialog");popup.tabIndex=-1;document.body.append(popup);}
    const parent=target.closest("dialog[open]") || document.body;
    if(popup.parentElement!==parent){if(popup.matches(":popover-open"))popup.hidePopover();parent.append(popup);}
    popup.replaceChildren();popup.setAttribute("aria-label","Help: "+info.title);
    const head=document.createElement("div");head.className="context-help-heading";
    const title=document.createElement("strong");title.textContent=info.title;head.append(title);
    const dismiss=document.createElement("button");dismiss.type="button";dismiss.className="context-help-close";dismiss.setAttribute("aria-label","Close help");dismiss.textContent="×";dismiss.onclick=()=>close(true);head.append(dismiss);popup.append(head);
    for(const [label,text] of [["What it does",info.purpose],["How to use it",info.how],["Example",info.example]]){const term=document.createElement("h4");term.textContent=label;const value=document.createElement(label==="Example"?"pre":"p");value.textContent=text;popup.append(term,value);}
    popup.hidden=false;popup.showPopover();place();
    if(focus)popup.focus();
  }
  function triggerFor(target) {
    const icon=document.createElement("span");icon.className="context-help-trigger";icon.setAttribute("aria-hidden","true");icon.setAttribute("aria-expanded","false");
    const mark=document.createElement("span");mark.textContent="?";icon.append(mark);
    icon.title="Help: "+studioHelpText(target)+" (F1 on the focused control)";
    icon.addEventListener("click",e=>{e.preventDefault();e.stopPropagation();if(current===icon)close();else show(icon,target,true);});
    triggers.set(target,icon);return icon;
  }
  function attach(node) {
    if(node.dataset.noContextHelp!==undefined)return;
    if(attached.has(node)){const icon=triggers.get(node);if(icon)icon.title="Help: "+studioHelpText(node)+" (F1 on the focused control)";return;}
    if(node.closest(".context-help-popup,.context-help-trigger"))return;
    if(node.matches("input[type=hidden],input[type=submit],#editor"))return;
    const label=node.labels?.[0];
    if(label && !label.classList.contains("sr-only")){if(attached.has(label)){attached.add(node);return;}attached.add(label);label.classList.add("has-context-help");label.append(triggerFor(node));attached.add(node);return;}
    if(node.matches("label"))return;
    if(node.matches("summary")){node.append(triggerFor(node));attached.add(node);return;}
    if(node.closest(".context-help-wrap")){attached.add(node);return;}
    const wrap=document.createElement("span");wrap.className="context-help-wrap";
    if(node.matches("textarea,input:not([type=checkbox]),select"))wrap.classList.add("context-help-field");
    node.before(wrap);wrap.append(node,triggerFor(node));attached.add(node);
  }
  function scan(){
    // Only changed subtrees are scanned; streamed text does not rescan every control.
    scheduled=false;if(current&&!current.isConnected)close();
    const roots=[...pending];pending.clear();
    for(const root of roots){if(!root.isConnected)continue;if(root.matches(selector))attach(root);root.querySelectorAll(selector).forEach(attach);}
  }
  new MutationObserver(records=>{
    for(const record of records)if(record.target.nodeType===1)pending.add(record.target);
    if(!scheduled&&pending.size){scheduled=true;requestAnimationFrame(scan);}
  }).observe(document.body,{childList:true,subtree:true});
  document.addEventListener("pointerdown",e=>{if(current&&!popup?.contains(e.target)&&!current.contains(e.target))close();});
  document.addEventListener("keydown",e=>{
    if(e.key==="Escape"&&current){e.preventDefault();e.stopImmediatePropagation();close(true);return;}
    const active=document.activeElement;
    if(!studioHelpKey(e,active)||!active||active===document.body||popup?.contains(active))return;
    const target=active.matches(selector)?active:active.closest(selector);if(!target)return;
    e.preventDefault();show(triggers.get(target)||target,target,true);
  },true);
  document.addEventListener("scroll",e=>{
    if(!current||popup?.contains(e.target))return;
    // Programmatic scrolls of unrelated panels (activity, conversation) must not close the popup.
    if(e.target===document||e.target.contains?.(current))place();
  },true);
  window.addEventListener("resize",()=>place());
  pending.add(document.body);scan();
}
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",initStudioHelp);
