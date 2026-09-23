# Apodex and Our Long-Term Approach

Verified from the public website on 22 September 2026. Website claims are not an independent operational test.

Apodex distinguishes between Deep Research, Deep Solve, and Deep Discover. It emphasizes preserving state, decisions, and constraints across weeks of work. [Official website](https://www.apodex.com/)

For Deep Discover, it describes asynchronous orchestration of up to 150 agents, a shared, continuously updated knowledge store, and separate controller contexts. [Architecture description](https://www.apodex.com/discover)

FrontierAgent is their public agent framework. Adopting the framework does not automatically grant access to all features of the hosted product or its models. [Official GitHub](https://github.com/ApodexAI)

## What We Implement in Miner

| Principle | Current Implementation |
|---|---|
| Persistent project state | SQLite: task brief, plan, dependencies, questions, answers, and attempts |
| Traceable evidence | Reports on disk, copies of accepted reports in the database, checksums of product files |
| Separate verification | New review session for each task and final product verification; model can be chosen separately |
| Automatic corrections | Findings are returned to the worker; after three failed attempts, a block is triggered |
| Recovery after interruption | Unique attempts, saved process identities, cleanup before restart, and limited retries |
| Human input | Highlighted questions and permanently stored answers, enabling independent continuation of work |

Our controller currently runs only one worker at a time. It does not support 150 parallel agents, a full conflict graph across sources, nor automatic rewriting of its own control code. We do not claim functional or performance parity with the hosted Apodex product.
