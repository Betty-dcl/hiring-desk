# Platform Security Engineer

*Source: jobs.ashbyhq.com/causaprima, text published by Causa Prima, retrieved on 2026-09-18.*

In this role, you will design and build the System Integrity Layer, establishing the foundational platform primitives that guarantee every agent action is isolated and attributable, every execution is traceable, every state transition is verifiable, and every software release satisfies strict security constraints by construction. This is a hands-on backend security engineering role where security, threat resilience, and compliance are natural outcomes of brilliant system architecture, not separate, bureaucratic processes. We are looking for a builder, not a governance operator—someone who treats trust guarantees as core system properties designed directly into the database and application layers from day one. Architecture or technical-leadership experience is fine, even welcome, as long as it comes with real hands-on depth you can point to—specific systems you designed and built—rather than process, documentation, or audit-standardization work alone.

What you'll do:

 - System Integrity Layer & Threat Modeling — Design the core execution model that governs how autonomous agents run, interact, and modify system state.

 - Proactive Threat Modeling — Conduct continuous, code-level threat modeling across our distributed networks to identify structural vectors, memory leakage, prompt manipulation, and authorization bypasses before a single line hits staging.

 - Auditability as a Native Property — Build append-only, tamper-resistant event systems that make every action reconstructable without relying on external logging frameworks or reactive "audit tooling."

 - Isolation, Trust Boundaries, & Adversarial Testing — Engineer strict execution and data isolation layers between agents, users, and workflows in a multi-tenant infrastructure.

 - Continuous Offensive Testing — Run active internal pentesting, adversarial chaos testing, and targeted red teaming exercises against our own infrastructure to aggressively expose flaws in isolation barriers and cryptographic verification boundaries.

 - Agent Execution Guarantees — Ensure all automated, agentic accounting processes are strictly attributable, deterministic where required, verifiable in hindsight, and unalterably constrained by explicit system rules.

 - Robust Engineering & Pipeline Security — Architect and refine our internal software delivery pipeline to guarantee absolute integrity from code commit to production deployment.

 - Build deterministic verification mechanisms into the CI/CD pipeline, ensuring that all third-party dependencies, agent libraries, and compiled services are cryptographically signed, scanned, and secure by default.

 - Compliance Through Architecture — Translate complex regulatory and audit requirements directly into low-level system constraints, completely eliminating the need for reactive operational processes or manual review workflows.

What we're looking for

 - Experience: 5+ years designing production backend platforms and distributed systems design.

 - Database experience: Deep schema design, RLS, and event-sourcing database expertise.

 - Security Architecture: Practical application-level security, encryption models, key management, and auth patterns.

 - Adversarial Mindset: You naturally think like an attacker to uncover race conditions, logic flaws, and vulnerabilities.

 - Policy-as-Code: Hands-on experience with Cerbos, OPA, Cedar, or similar.

 - Domain knowledge: You've owned real breadth of the security stack, not one narrow slice of it. You understand what securing mission-critical enterprise software at large scale demands. Regulated industries (fintech, banking, insurance) are a plus, but end-to-end ownership counts for more than time spent inside a large org.

Nice to have: 

 - Cryptographic primitives

 - Event streaming (Kafka/Redpanda)

 - Kubernetes and cloud-native architecture knowledge

 - LLM/agentic system security exposure (prompt injection defense, guardrails, agent threat modeling)—relevant to our product surface, but a plus on top of the backend and security fundamentals above, not a substitute for them.
