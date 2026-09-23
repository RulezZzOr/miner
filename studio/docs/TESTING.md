# Testing a Studio installation

Open the Studio URL on the host or LAN. Paths in **Open project** refer to the computer running Studio. Start in a new project folder so test output is easy to inspect.

1. **Editor persistence:** use **New file**, enter `notes.md`, type a short note and save. Reload the page, reopen the file and check its contents.
2. **Real model execution:** select an available model and ask it to create a small source file and run a test. Inspect both the saved file in **Files** and the actual test output in **Activity** or **Console**. A model's written assertion is not proof that a file exists or a test passed.
3. **Approval:** leave **Enable automatic actions** off for the first run. A file write should appear in **Approvals and responses** and the top bar. Inspect the action, then choose **Yes**, **No**, or send feedback. Feedback does not approve the action.
4. **Office and live map:** open **3D Office** while a run is active. Select its desk, inspect the model and latest activity, then open the run or live map. A paused or blocked task should not appear as actively working.
5. **Long-running project:** in **AI Projects**, supply concrete acceptance criteria and executable checks. Inspect the plan, build, review and verification results. Accept the work only after checking the result. An ordinary run finishing is different from a product being accepted.
6. **Company Driver:** create or select a company, attach the intended projects and inspect its limits. Start with one bounded task before adding repetition. Confirm how tool approval and automatic acceptance are configured.

The workspace stays on the Studio host when the browser closes. The host and Studio service must remain running for work to continue. Closing a browser does not stop a server job. Use **Stop** or **Pause Driver** when you want execution to stop.

A task that reaches its time, turn or run limit is incomplete. Inspect the recorded reason and last real activity before retrying. Model availability, generation speed and review quality depend on the selected server; an HTTP health response alone does not test these.

This is an alpha release. Windows uses WSL2. Optional provider login requires the owner's account; it is not verified by a local model test. Company departments describe work organization, and the current Driver shares one worker slot.
