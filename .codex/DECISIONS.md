# Durable Decisions

## Phase 4A image splitting

- Equal split boundaries use integer floor edges (`length * index // count`). This covers every source pixel exactly once and keeps panel order deterministic.
- Splitting occurs after the Quick tab's existing transform/explicit resize settings and before per-panel encoding. No split-only resize or alternate format path is introduced.
- The existing batch worker and destination/format settings are reused. One source failure does not stop later sources, and a partially written panel group is rolled back.
- Preview guides are a separate graphics overlay. The boundary function can later be replaced by custom positions without changing image encoding or export naming.

## Vertical density rules

- Shared editable controls target a 34 px logical height; auxiliary compact actions may use 28 px when their font still has at least 10 px of vertical allowance.
- Primary actions remain visually larger: Quick save is 40 px and Edit/Upscale/Thumbnail execution buttons are 48–50 px.
- The Edit save inspector is vertically scrollable at short window heights and has no horizontal scrollbar; at the normal 720 px window height its content fits without vertical scrolling.
- Density is reduced through padding, margins, and spacing rather than smaller Japanese fonts.

## Phase 4B interaction and layout rules

- The hand-draw eyedropper is an ephemeral UI tool, not a persisted `HandStroke` tool. Sampling updates the brush color preference without adding Undo history.
- Hand color sampling composites the processed preview and committed hand overlay, while preserving the separately controlled pen opacity. Explicit picker use returns to Pen; Alt-click temporarily samples without changing the selected drawing tool.
- The existing transparency eyedropper remains a separate input mode and continues sampling the processed image without the hand overlay.
- Quick and Edit use shared primary settings-pane widths (250 px default/minimum, 340 px maximum) and give resize stretch to the center preview.
- Edit save options use a two-column grid with matching label/control visibility and an ignored horizontal size policy so vertical-scrollbar appearance cannot create horizontal overflow.

## Phase 4C image splitting acceptance

- Reuse the existing Phase 4A split core, batch worker, naming, destination, and encoding paths; do not introduce a second split-only export pipeline.
- Quick preview zoom is explicit view state: `None` means fit, while `1.0` and `2.0` mean 100% and 200%. Image refresh preserves the selected fixed zoom and guides remain scene-coordinate overlays with cosmetic pens.
- Do not add panel thumbnails in the compact three-column workspace. The source overlay guides, ordered filename preview, and saved-panel verification provide sufficient confirmation without reducing the central preview or increasing left-pane density.
