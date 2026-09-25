# Miner — 24/7 company mind map

Miner (Switch Studio) is a persistent work controller for an AI-run company. Its always-on web service makes the control plane available; the Company Driver schedules eligible work while its configured limits and human decision gates allow it. A running server alone does not mean agents are currently working.

```mermaid
mindmap
  root((Miner / Switch Studio))
    Company direction
      Company Builder
        Goals and policies
        Projects and departments
        Worker and reviewer profiles
      Work queue
        Brief and done criteria
        Priority and dependencies
        Recurring schedule
        Run, step, time and horizon limits
    Always-on control plane
      Linux system service
        Start on boot
        Restart after process failure
        Keep host awake and network reachable
      Studio API and dashboard
        Live status and 3D office
        Decisions and approvals
        Company and mission controls
      Persistent state
        SQLite company and task history
        Checkpoints and recovery records
        Workspace files and reports
    Company Driver loop
      Observe queue and executions
      Check eligibility
        Driver enabled
        Within horizon and budgets
        Dependencies complete
        Worker slot available
        Model endpoint healthy
      Reserve execution atomically
      Plan bounded steps
      Execute worker tools
      Independent reviewer
      Run configured verification
      Return failures for bounded fixes
      Save evidence and outcome
      Tick again for next eligible task
    Human control gates
      Answer questions and blocked decisions
      Tool permission is separate from result acceptance
      Review evidence before accepting unverified work
      Pause or resume company
    Operational truth
      Service active means UI and controller are reachable
      Driver active plus eligible queue means work can start
      Approval, failure, budget, dependency or model outage can stop progress
      One shared worker slot limits parallelism
      A seven-day unattended run is not yet validated
```

## What must be true for continuous work

1. The host boots the Studio service and automatically restarts it after failure. The machine must stay awake, the data volume must remain available, and the network/model endpoints must be reachable.
2. A company has an enabled Driver, a useful queue, explicit completion criteria, adequate run/time/horizon limits, and worker and reviewer profiles that actually respond.
3. The dashboard and alert path make stalled work visible. The owner can resolve questions, approvals, expired horizons, exhausted limits, and repeated controller errors.
4. Recovery preserves state and does not silently repeat unsafe external actions. Tool approval and accepting a finished result remain separate choices.

The loop supports recurring work and continued scheduling, but it does not invent business priorities or new projects after the approved queue is empty. It cannot promise uninterrupted inference or guaranteed delivery. Keep a human accountable for strategy, permissions, customer commitments, and final acceptance. The controller's integration tests verify scheduling and recovery mechanics; they do not prove a week-long autonomous company run.
