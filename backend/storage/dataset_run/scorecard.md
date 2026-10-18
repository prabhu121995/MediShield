# Dataset run: claim pipeline scorecard

Run at 2026-10-17 22:45. One claim per case cluster, uploaded through `POST /api/claim-bundles` with no document types (the classifier decides).

## Headline

| Measure | Result |
|---|---|
| Claim decision accuracy | 0.0% (0/1) |
| Page classification accuracy | 100.0% (5/5) |
| Fraud tag recall (injected fraud clusters) | n/a |
| Baseline: always APPROVE | 100.0% (1/1) |

## Per-class recall (the imbalance trap: read these, not only the headline)

| Expected | Recall | Predicted as |
|---|---|---|
| APPROVE | 0.0% (0/1) | ESCALATE 1 |
| REJECT | n/a |  |
| ESCALATE | n/a |  |

## Confusion matrix (rows expected, columns predicted)

| | APPROVE | REJECT | ESCALATE |
|---|---|---|---|
| **APPROVE** | 0 | 0 | 1 |
| **REJECT** | 0 | 0 | 0 |
| **ESCALATE** | 0 | 0 | 0 |

## Classification by document type

| Type | Accuracy |
|---|---|
| CLAIM_FORM | 100.0% (1/1) |
| DISCHARGE_SUMMARY | 100.0% (1/1) |
| ID_DOCUMENT | 100.0% (1/1) |
| POLICY_AMENDMENT | 100.0% (1/1) |
| PRESCRIPTION | 100.0% (1/1) |

## Every claim

| Claim | Expected | Why | Predicted | Conf. | Fraud tags | Justification |
|---|---|---|---|---|---|---|
| C_016 | APPROVE | clean | **ESCALATE** ✗ | 0.913 | date_conflict | Escalated to human review: fraud score 0.60 (date_conflict); KYC failed: no member with policy NPI-123456789, MED-GOLD-1744692, MED-GLD-1744662, MED-GOLD-1744682; procedure not covered: ; schema invalid: claim_PT_50538.p |
