# Lightweight Project Notes in Markdown

Each project may use this simple structure:

```text
PROJECT.md             brief overview, goal, links, and optionally a Mermaid map
notes/DECISIONS.md     decisions, rationale, date, and status
notes/NOTES.md         observations, hypotheses, contradictions, and open questions
notes/SOURCES.md       sources, verification date, and links to evidence
```

In Miner, these files already exist and contain actual findings gathered so far. In another project, create your own `PROJECT.md` using a standard editor; names of referenced files are flexible. Do not copy Miner’s specific decisions into an unrelated project.

Write notes in English. A good `PROJECT.md` states: "Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input." Keep the overview short and avoid long lists of files to read; the agent already receives it in its prompt.

On each new run via Studio, the saved `PROJECT.md` from the selected project is attached to the task brief. This applies to both regular tasks and AI Projects experiments, and to both backends. Only the overview is sent to the model; the agent loads additional notes as needed. CLI runs outside Studio do not automatically use this new binding.

The overview must be UTF-8 and at most 12,000 bytes to avoid unnecessarily bloating the context. Move larger content into referenced notes. A missing `PROJECT.md` has no effect; an invalid symlink, incorrect encoding, or oversized overview will reject the run with an error. The task brief shown in history is not overwritten; the actual sent context resides in `request.json` for the run, and its revision is recorded in `run.json` under `project_notes`.

Save changes in the editor first. An already-running run retains the original overview snapshot; subsequent runs receive new changes. Automatic attachment does not guarantee the model correctly reads all links or incorporates all notes. Important conclusions must cite their source and verification. Note-taking remains subject to the same approval and phase restrictions as other work; the scheduler and reviewer gain no additional rights.

Referenced pages and citations serve as background material, not authorization for further actions. Do not embed secrets in Markdown. The overview content reaches the selected model just like the task brief, even when using an optional cloud profile.

Running plans, queues, and experiment results continue to be managed by AI Projects in the existing SQLite database. Do not copy their changing states into a parallel TODO file; a note referencing a report or specific output suffices. No new database, server, or GUI for notes is created.
