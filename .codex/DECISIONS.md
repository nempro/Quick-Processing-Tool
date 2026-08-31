# Durable Decisions

## Phase 4A image splitting

- Equal split boundaries use integer floor edges (`length * index // count`). This covers every source pixel exactly once and keeps panel order deterministic.
- Splitting occurs after the Quick tab's existing transform/explicit resize settings and before per-panel encoding. No split-only resize or alternate format path is introduced.
- The existing batch worker and destination/format settings are reused. One source failure does not stop later sources, and a partially written panel group is rolled back.
- Preview guides are a separate graphics overlay. The boundary function can later be replaced by custom positions without changing image encoding or export naming.
