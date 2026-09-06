# AI Collaboration Note

## Purpose

AI assistance was used as an engineering accelerator, not as an authority over architecture, correctness, or production readiness.

## AI-Assisted Areas

AI assistance was used for:

- Architecture exploration
- Schema design
- Prompt design
- Evaluation-case generation
- Test-case ideation
- Documentation drafting
- Code generation
- Failure-mode brainstorming

## Human Ownership

The engineer remains responsible for:

- Architecture decisions
- Security boundaries
- API contracts
- Data model decisions
- Validation rules
- Approval controls
- Integration behavior
- Test execution
- Evaluation results
- Deployment decisions

## Verification Process

AI-generated implementation is treated as untrusted code.

Verification consists of:

1. Static inspection
2. Python compilation
3. Linting
4. Unit tests
5. Integration testing
6. Manual API verification
7. Adversarial evaluation
8. Regression testing

## Important Principle

An AI-generated answer that sounds correct is not considered evidence that the implementation is correct.

The project explicitly separates:

```text
AI suggestion
    ↓
Engineering review
    ↓
Automated verification
    ↓
Measured behavior
```

## Prompt Injection Handling

Meeting notes are untrusted customer data.

The system prompt explicitly instructs Gemini not to treat instructions embedded in notes as system instructions.

Application validation provides an additional boundary.

## Model Limitations

Structured output guarantees format compliance, not truth.

Therefore the application validates:

- Evidence
- Required fields
- Business rules
- Grounding
- Execution state

This follows Google's guidance that structured output should still be validated by application code for semantic correctness. citeturn0search1
