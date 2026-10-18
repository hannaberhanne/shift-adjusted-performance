# Method

Every rule, formula, weight and threshold the pipeline uses. The constants live at the top of each stage file, so this page and the code can be checked against each other.

"Full coverage" means a technician's actions are in the full audit export. Technicians who only appear in a partial PDF export are kept as context but never scored or used in a baseline.

## 1. Evidence rules (stage 1)

| Audit action | Rule | Counts as |
|---|---|---|
| `Comment added` | Follow-up valid (Comment added) | strength |
| `State Responded -> Waiting on User` | Follow-up valid (Waiting on User) | strength |
| `State Assigned -> Responded` | Initial handling response | strength |
| `State Assigned -> Resolved` | Resolved ticket | strength |
| `State Assigned -> Resolved` on a Password maintenance ticket, within 15 minutes of the technician's first touch | Password template quick resolution | strength |
| `Resolution code set` | Closure documentation | strength |
| `Assignee ... -> Front Desk Queue` | Assigned to Front Desk Queue | strength, counted separately |
| `Assignee ... -> <another team>` | Rerouted to correct department/person | strength |
| `Category changed` | Category correction needed | issue |
| `Subcategory changed` | Subcategory correction needed | issue |
| `State Resolved -> Reopened by Client` | Reopened ticket | neutral by policy |
| `Comment edited` | Comment edited after posting | watched, not scored |
| any row from the partial PDF export | PDF-only partial context | neutral, never scored |

Per-technician metrics, from full-coverage actions only:

| Metric | Definition |
|---|---|
| UpdatesPerHour | actions / scheduled hours |
| IncidentsPerHour | unique incidents touched / scheduled hours |
| OnShiftPct | share of actions that happened during one of the technician's scheduled slots |
| FollowupPerIncident | valid follow-ups / unique incidents |
| AccuracyCorrections | category + subcategory corrections |
| ShortComments | comments under 15 characters (watched) |

Scheduled hours come from the schedule: one hour per slot the technician is on, for every date in the window.

The evidence sample used by stages 2, 4 and 5 is up to 20 actions per technician, spread evenly across their activity.

## 2. Schedule context (stage 2)

For every sampled action:

- **Night:** the action happened at 6 pm or later.
- **Weekend:** Saturday or Sunday.
- **On slot:** the technician was scheduled in the slot the action falls in.
- **Team size and co-scheduled peers:** who else was on that slot. A team size of 1 is a solo shift.
- **Slow ticket:** the incident's observed span (first to last observed action) is at or above the 75th percentile of non-zero spans in the sample. The 90th percentile is also recorded.

## 3. Benchmark (stage 3)

The benchmark is the full-coverage technician with the highest `UpdatesPerHour / team max + IncidentsPerHour / team max`.

- **Productivity %** = mean(updates/hr ÷ benchmark, incidents/hr ÷ benchmark) × 100
- **Execution %** = mean(follow-up per incident ÷ benchmark, on-shift % ÷ benchmark) × 100
- **Quality** = "At/Better" if corrections per 100 incidents ≤ the benchmark's, otherwise "Worse"

| Band | Productivity % |
|---|---|
| Meets standard | 85 or more |
| Approaching standard | 65 to 85 |
| Below standard | 45 to 65 |
| Critical gap vs standard | under 45 |
| No score (data gap) | partial coverage |

Gaps are listed in this order: low throughput (productivity under 50%), low on-shift alignment (under 85% of the benchmark's), follow-up below benchmark (under 70% of the benchmark's), higher correction rate than the benchmark.

## 4. Standardization (stage 4)

Each metric becomes a z-score using the mean and population standard deviation of full-coverage technicians only. Metrics where lower is better have their sign flipped, so a higher z always means stronger performance.

| Component | Metrics (equal weight) |
|---|---|
| Productivity | UpdatesPerHour, IncidentsPerHour |
| Execution | FollowupPerIncident, OnShiftPct, CorrectionsPer100Incidents (lower is better), IssueRateSample (lower is better) |
| Context load | NightEventShare, WeekendEventShare, SlowIncidentNonZeroP75EventShare, SoloEventShare |

- **Core performance** = mean(Productivity, Execution)
- **Context-adjusted performance** = Core performance + Context load

Context load is a separate component on purpose. It shows how hard someone's shifts were instead of quietly changing their other metrics.

Stage 4 also writes how each technician performs while scheduled with each peer, and a weekday/weekend by time-of-day breakdown (morning 8 to 11, afternoon 12 to 4, evening 5 to 7, night 8 to 11).

## 5. Scorecard (stage 5)

Each metric score is `PERCENTRANK.INC(full-coverage values, value) × 100`, or `(1 − PERCENTRANK.INC) × 100` when lower is better. Component scores are weighted means of their metric scores.

**Queue and ticket handling (60% of overall)**

| Metric | Direction | Weight |
|---|---|---|
| UpdatesPerHour | higher | 0.20 |
| IncidentsPerHour | higher | 0.20 |
| FollowupPerIncident | higher | 0.15 |
| OnShiftPct | higher | 0.15 |
| OnScheduledEventRate | higher | 0.10 |
| QueueMeanMinutesSincePrevAction | lower | 0.10 |
| QueueIssueRate | lower | 0.10 |

**Knowledge and accuracy (40% of overall)**

| Metric | Direction | Weight |
|---|---|---|
| CorrectionsPer100IncidentsCalc | lower | 0.30 |
| IssueRateSample | lower | 0.25 |
| CategoryCorrectionRate | lower | 0.15 |
| SubcategoryCorrectionRate | lower | 0.10 |
| ReroutePrecisionRate | higher | 0.10 |
| KnowledgeIssueRate | lower | 0.10 |

**Overall** = 0.60 × Queue + 0.40 × Knowledge. Partial-coverage technicians get no score.

**Confidence**

`score = 100 × coverage × (0.4 × min(1, events/20) + 0.3 × min(1, incidents/10) + 0.3 × (0.5 × min(1, queue entries/10) + 0.5 × min(1, knowledge entries/6)))`, where coverage is 1 for full and 0.5 for partial.

| Tier | Requires | Use |
|---|---|---|
| High | full coverage, 20+ events, 10+ incidents, 10+ queue and 6+ knowledge entries | scorecard decisions |
| Medium | full coverage, 10+ events, 5+ incidents, 5+ queue and 3+ knowledge entries | use with caution; confirm with more evidence |
| Low | anything else | evidence only; not for ranking decisions |

## 6. Check-in (stage 6)

Each technician is rated against role expectations, not against each other.

| Domain | Meets Expectations | Needs Support | Data used |
|---|---|---|---|
| Queue & Ticket Handling | incidents/hr ≥ 0.60, follow-up ≥ 0.50, on-shift ≥ 90%, and 5+ handled transitions or 8+ reroutes | incidents/hr ≥ 0.25, follow-up ≥ 0.30, on-shift ≥ 80% | tickets |
| Accuracy & Knowledge | ≤ 4 corrections per 100 incidents | ≤ 10 | tickets |
| Phones & Calls | | | none yet: always Insufficient Data |
| Front Desk & Printers | 5+ Front Desk Queue assignments | 1 to 4 | proxy only |
| Classroom/Media Readiness | | | none yet: always Insufficient Data |
| Reliability & Consistency | on-shift ≥ 95% | ≥ 85% | proxy only |
| Communication Responsiveness | on-shift ≥ 95% and follow-up ≥ 0.50 | on-shift ≥ 85% and follow-up ≥ 0.30 | proxy only |

Anything below Needs Support is Needs Immediate Improvement. Proxy ratings are labelled "(Proxy)" in the workbook.

**Outcome**

- Insufficient Data if coverage is partial.
- Performance Concern if two or more of Queue, Accuracy and Communication are Needs Immediate Improvement.
- Needs Support Plan if two or more of those three are below Meets.
- On Track otherwise.

**Packet risk checks:** 5+ accuracy corrections, on-shift under 85%, 5+ short comments, or 3+ comment edits. Each cites up to six incident numbers.
