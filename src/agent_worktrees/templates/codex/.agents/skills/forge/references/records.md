# Forge Record Formats

## Specification

```markdown
# Feature: <title>

## Problem

## Intended User Outcome

## Scope

## Explicit Exclusions

## User Flows

## States and Edge Cases

## Product Decisions

## Acceptance Criteria

- AC-1: ...

## Sequential Task Breakdown

## Required Validation

## Open Questions
```

Acceptance criteria must be observable. Do not encode unresolved questions as requirements.

## Task Packet

```markdown
# Task: <id> — <title>

## Objective

## Owner

## Applicable Acceptance Criteria

## Hard Dependencies

## Owned Area

## Must Not Change

## Decisions and Constraints

## Required Validation

## Expected Completion Report
```

Do not include an assigned worktree. Every task uses the worktree recorded when Forge started.

Use `terra` for backend, application, data, integrations, nonvisual logic, tests, and repairs. Use
`kimi` for substantial visual frontend work.

## Technical Challenge

```markdown
# Technical Challenge

## Specification Reviewed

## Confirmed Blocking Findings

## Confirmed Nonblocking Findings

## Recommended Changes

## Repository or Provider Evidence

## Product Questions for Fable
```

This is the only pre-implementation Sol review. Fable and the user reconcile it; Sol does not review
the specification again.

## Escalation Packet

```markdown
# Diagnosis Request: <task or failing test>

## Current Phase

## Exact Task or Test

## Applicable Acceptance Criteria

## Current Branch and HEAD

## Current Diff

## Failing Command

## Complete Relevant Output

## Reproduction Steps

## Environmental Facts

## Changes Attempted

## Requested Sol Output
```

Ask Sol for root cause and a bounded implementation recommendation. Sol remains read-only.

## Sol Diagnosis

```markdown
# Sol Diagnosis

## Root Cause

## Evidence

## Recommended Repair

## Files or Boundaries Affected

## Verification to Rerun

## Remaining Uncertainty
```

Terra applies the recommendation. Kimi may apply it only when the blocked task is specifically
visual frontend work.

## Implementation Decision or Specification Deviation

```markdown
# <Decision or Deviation>

## Task

## Summary

## Reason

## Sol Diagnosis or Other Evidence

## Specification and Acceptance-Criteria Impact

## Verification Impact
```

Record any intentional departure from the approved specification before continuing. Forge keeps
the summary, reason, and optional full source in its canonical memory through every compaction.

## Validation Evidence

Record:

- validation name;
- exact command or browser procedure;
- pass, fail, or blocked;
- timestamp;
- invocation worktree and committed HEAD;
- output/evidence path;
- environment;
- task coverage;
- responsible repair owner for a failure.

Every result is bound to the committed branch HEAD. Later implementation or repair commits make
older evidence stale.

Visual evidence includes screenshots, viewport, tested interactions, console errors, and failed
network requests.

## Worker Completion Report

```markdown
# Worker Completion Report

## Task

## Summary

## Files Changed

## Decisions Made

## Validation Run

## Validation Results

## Remaining Risks

## Scope Questions
```

Task reports do not require separate task commits. Sequential workers share the current checkout.
The coordinator commits the complete implementation before the verification phase.

## Final Completion Report

```markdown
# Forge Completion

## Delivered Scope

## Final Worktree and Branch

## Final Commit

## Validation Evidence

## Resolved Diagnoses

## Remaining Nonblocking Risks
```
