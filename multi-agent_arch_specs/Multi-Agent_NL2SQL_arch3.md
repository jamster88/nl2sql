## **Deep-Dive Multi-Agent Architecture**

For a production-grade, schema-agnostic system targeting [**PostgreSQL**](https://www.postgresql.org/), must isolate execution environments, secure the database layer, and manage state transitions. The pipeline uses an **asynchronous state machine** where agents communicate via structured messages, preventing cascading failures.

                      `[ Raw Natural Language Query ]`  
                                     `│`  
                                     `▼`  
 `┌───────────────────────────────────────────────────────────────────────┐`  
 `│ 1. INTAKE & DATA MAPPING STAGE (State: Context Gathering)             │`  
 `│                                                                       │`  
 `│   ┌───────────────────────┐         ┌───────────────────────────────┐ │`  
 `│   │ Supervisor Agent      ├────────►│ Vector-RAG Schema Agent       │ │`  
 `│   │ - Classifies query    │         │ - Queries metadata vector db  │ │`  
 `│   │ - Starts state machine│         │ - Computes Cosine Similarity  │ │`  
 `│   └───────────────────────┘         └──────────────┬────────────────┘ │`  
 `│                                                    │                  │`  
 `│                                                    ▼                  │`  
 `│   ┌───────────────────────┐         ┌───────────────────────────────┐ │`  
 `│   │ Context Aggregator    │◄────────┤ Fuzzy Match Agent             │ │`  
 `│   │ - Merges state pieces │         │ - Trigram search on enums/IDs │ │`  
 `│   └──────────┬────────────┘         │ - Exposes raw literal mappings│ │`  
 `└──────────────┼────────────────────────────────────────────────────────┘`  
                `│`  
                `▼ [ Pruned DDL + Valid Row Literals + Context State ]`  
 `┌───────────────────────────────────────────────────────────────────────┐`  
 `│ 2. SYNTHESIS & STATISTICAL ANALYSIS STAGE (State: Compilation)        │`  
 `│                                                                       │`  
 `│   ┌───────────────────────────────────────────────────────────────┐   │`  
 `│   │ SQL Generator Agent (LLM Reasoning Loop)                       │   │`  
 `│   │ - Assembles Few-Shot Golden Pairs based on schema similarity  │   │`  
 `│   │ - Generates dialect-specific PostgreSQL source code           │   │`  
 `│   └───────────────────────────────┬───────────────────────────────┘   │`  
 `└───────────────────────────────────┼───────────────────────────────────┘`  
                                     `│`  
                                     `▼ [ Raw SQL String ]`  
 `┌───────────────────────────────────────────────────────────────────────┐`  
 `│ 3. DECOUPLED VALIDATION & REPAIR LOOP (State: Guardrails)             │`  
 `│                                                                       │`  
 `│                   ┌──────────────────────────────┐                    │`  
 `│                   │ SQL Query Validator Agent    │                    │`  
 `│                   │ - Non-executing AST Parser   │                    │`  
 `│                   │ - Regex Matcher (No Write)   │                    │`  
 `│                   └──────────────┬───────────────┘                    │`  
 `│                                  │                                    │`  
 `│                    Passed? ──────┴────── Regressed?                   │`  
 `│                   ┌───────              ────────┐                     │`  
 `│                   ▼                             ▼                     │`  
 `│       ┌───────────────────────┐     ┌───────────────────────┐         │`  
 `│       │ Database Engine Pass  │     │ SQL Repair Agent      │         │`  
 `│       │ - EXPLAIN ANALYZE run │     │ - Analyzes AST error  │         │`  
 `│       │ - Cost / Timeout check│     │ - Increments retry counter      │`  
 `│       └───────┬───────────────┘     └───────────┬───────────┘         │`  
 `│               │                                 │                     │`  
 `│      OK? ─────┴───── Fail                       ▼                     │`  
 `│     ┌───             ────┐             [ Max 3 Retries Loop ]         │`  
 `│     ▼                    ▼                      │                     │`  
 `│  [ Execute ]     [ Send Error Stack ] ──────────┘                     │`  
 `└─────┬─────────────────────────────────────────────────────────────────┘`  
       `│`  
       `▼ [ Raw Result Set (Rows / Columns Payload) ]`  
 `┌───────────────────────────────────────────────────────────────────────┐`  
 `│ 4. PRESENTATION & AUDIT LAYER (State: Formatting)                     │`  
 `│                                                                       │`  
 `│   ┌───────────────────────────────────┐  ┌────────────────────────┐   │`  
 `│   │ Visual Formatting Agent           │  │ Insight Audit Agent    │   │`  
 `│   │ - Formats rows to Markdown tables │  │ - Audits math vs rows  │   │`  
 `│   │ - infers chart configuration schemas │ - Checks data leakages│   │`  
 `│   └───────────────────────────────────┘  └────────────────────────┘   │`  
 `└───────────────────────────────────────────────────────────────────────┘`

## **Detailed Agent Breakdown**

> 1. **Supervisor Agent:** Validates structural input bounds. It determines if the question is answerable given the macro domain bounds and rejects explicit cross-site scripting or prompt injection vectors before processing.  
> 2. **Vector-RAG Schema Agent:** Eliminates column-bloat by scanning structural DDL embeddings. It extracts only matching database elements (up to 10 context tables) and their logical dependencies (Foreign Keys), building a minimal, deterministic relational schema graph.  
> 3. **Fuzzy Match Agent:** Avoids literal failures by executing fast Trigram string distances (pg\_trgm) or Levenshtein calculations against lookup parameters, replacing vague human descriptors with precise database keys.  
> 4. **SQL Generator Agent:** Translates the intent using high-reasoning execution prompts. It injects specific database context rules (e.g., timezone alignment, explicit column casting, or missing indexing hints) to assemble the final target query.  
> 5. **Query Validator Agent:** Inspects code safety completely decoupled from the real database infrastructure. It uses an AST parser to ensure no modification statements (INSERT, UPDATE, DROP, ALTER) can pass through, filtering out complex multi-statement query schemes.  
> 6. **SQL Repair Agent:** Operates a structured, self-healing reflection loop. It catches structural database tracebacks, translates runtime errors (like misaligned aggregation grouping identifiers), tracks the error history stack, and crafts specialized prompt directives to correct the query.