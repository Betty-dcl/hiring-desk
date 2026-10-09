# Senior Backend / Platform Engineer

*Source: jobs.ashbyhq.com/causaprima, text published by Causa Prima, retrieved on 2026-09-18.*

You'll own the platform our agents run on — event-driven architecture, data pipelines, integrations with customer accounting systems, and the infrastructure that makes everything reliable, auditable, and fast.

What you'll do

 - Event store — Append-only PostgreSQL with tamper-evident hash chaining, real-time event distribution, agent replay and recovery, schema versioning.

 - Cryptographic signature infrastructure — Key management, signature verification on all agent events, approval chains for external actions.

 - Workflow Constraint Engine — The gateway that verifies authorization before any external action (email, payment, API call) proceeds.

 - PII Vault & GDPR compliance — Isolated, encrypted storage for personal data. Right to erasure. Data residency per region.

 - Multi-tenancy — Row-level security, tenant isolation across all data stores, tenant-scoped LLM API keys.

 - Authorization (Cerbos) — Attribute-based access control for agents and humans. Deterministic policy evaluation — LLMs never make auth decisions.

 - MCP security layer — Authentication, capability-based scoping per partner, and rate limiting for the Model Context Protocol endpoint.

 - Database architecture — PostgreSQL schema design, indexing strategy, performance tuning, read replica topology.

What we're looking for

 - 3+ years experience with Python and/or TypeScript in production.

 - 5+ years designing backend platforms for production systems.

 - Strong systems design skills — this is the primary requirement. You think in boundaries, contracts, failure modes, and scaling characteristics.

 - Deep PostgreSQL knowledge — RLS, indexing tradeoffs, event sourcing patterns, schema evolution.

 - Strong application security architecture skills: encryption models, key management, auth patterns.

 - Experience with policy-as-code authorization (Cerbos, OPA, Cedar, or similar).

 - Strong code review skills for security-sensitive AI-generated code.

 - Nice to have:
   
   - Cryptographic primitives in production (digital signatures, hash chaining, application-layer encryption).
   
   - Event streaming platforms (Kurrent, Redpanda, Kafka).
   
   - SOC 2 or GDPR compliance engineering.
   
   - Fintech or regulated-environment background.
