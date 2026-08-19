# Project Scope

## Product responsibility

Architecture Review Board is a structured architecture-review system. It evaluates
software/system architecture proposals through multiple independent specialist review
perspectives and produces a structured review outcome.

It is not:

- a chatbot
- a coding agent
- an autonomous architect
- a code generator
- a ticketing system
- an incident-management system
- a retrieval system
- a generic agent framework

The core product responsibility:

    architecture proposal
        -> specialist findings
        -> explicit disagreement/consensus
        -> structured review decision

The system supports human engineering review. It does not replace organizational
accountability, and its output is advisory rather than an authorization to deploy.

## v0.1.0 scope

In scope:

- structured architecture review request
- five independent specialist review dimensions
- independent structured findings
- explicit risk/severity representation
- structured supervisor decision
- explicit disagreement representation, not forced consensus
- an evidence/provenance boundary, kept generic across reference integrations
- a structured hosted-model reference provider, optional and provider-neutral at the core
- deterministic evaluation of review behavior against a fixed golden dataset
- a local CLI exposing one review and one evaluation run

Out of scope for the foreseeable future:

- code generation
- autonomous deployment
- issue/ticket creation
- incident response
- architecture diagram generation
- arbitrary web browsing
- remote tools
- a generic multi-agent platform
- a chat interface
- automatic architecture enforcement
- replacing human architecture governance
- a persistent workflow engine
- project management integration

## Architectural principle

The domain models independent specialist assessments plus explicit structured
reconciliation, not agents talking to each other. Specialist reviewers consume the same
bounded architecture proposal and produce structured findings independently; a supervisor
inspects those findings and produces a decision. No conversational transcript is required
for correctness, so there is no `Message`, `Conversation`, `AgentChat`, `Turn`, `Dialogue`,
or shared scratchpad model, and no generic `Agent`/`Tool`/`AgentMessage` abstraction.
`ReviewDimension` values name a review responsibility, not a persona.

## High-level architecture target

    ArchitectureReviewRequest
            |
            v
    ReviewCoordinator
            |
            +--> ReliabilityReviewer
            +--> SecurityReviewer
            +--> DataReviewer
            +--> OperabilityReviewer
            +--> ComplexityReviewer
            |
            v
    Structured ReviewFindings
            |
            v
    ReviewSupervisor
            |
            v
    ArchitectureReviewResult

An optional adapter provides engineering-document evidence through an external knowledge
boundary, without this system depending on that boundary's internal shape.

## Trust boundary

`ArchitectureReviewRequest` and `ReviewEvidence` content is untrusted, proposal-author- or
external-source-supplied data. No command execution happens anywhere in this system, and
no proposal or evidence text is treated as an instruction. The concrete hosted-model
adapter keeps this data separated from its own trusted system instructions, never folding
proposal or evidence content into that trusted channel. See
[docs/trust-boundaries.md](docs/trust-boundaries.md) for the full breakdown of controls.

## Relationship to other personal projects

The system can optionally consume engineering knowledge from an independent retrieval
system through the evidence boundary described above, and may later emit telemetry to a
separate AI observability platform.

## Project origin

This project concept and its initial scope were defined before the start of my next
employment engagement.
