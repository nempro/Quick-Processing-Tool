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

## Quick save destination result UX

- Normal, split, and batch Quick exports share one result contract. The worker reports an output folder only after an output has been written successfully; total failure never presents a successful destination action.
- `保存先とプライバシー` shows the planned destination both while expanded and as a compact collapsed summary. Long paths are elided for layout safety and remain available in full through tooltips.
- A compact result card outside the accordion retains the last successful output count and actual folder access. Multiple successful folders are represented as multiple destinations and `保存先を開く` opens each one.
- Starting a new file-save operation or using `すべてクリア` clears stale save-result state. Clipboard-only processing does not create a save result.

## Phase 4D operation placement and result handoff

- Use shared visual operation roles for primary execution, secondary actions, and save-result cards while preserving tool-specific action wording where it conveys the operation.
- Keep reset and clear in sticky settings footers and keep target-list add/clear actions adjacent to the list. Save results pair the result summary, actual destination, and open-folder action.
- Pass a verified, already auto-saved Upscale output through a small `(path, target)` signal. This reuses the current file-path processing architecture without introducing another temporary file or a workflow engine.
- An Upscale → Image Split handoff replaces only the Quick target queue, preserves Quick settings and the Upscale page result, sets the shared Current Source to the result, and opens the default vertical four-way split.
- Apply the common 250 px settings-pane baseline to compatible tools. Retain Thumbnail's accepted 30/43/27 layout because forcing it to 250/340 breaks its title-entry workflow.

## Phase 4E custom split boundaries

- Store custom boundaries as strictly increasing normalized ratios, not Preview or source pixels. The same ratios therefore survive Fit/100%/200%, viewport resize, source thumbnails, output resize, and differing Batch dimensions.
- Keep `None` as the equal-split state so existing equal edge rounding remains byte-for-byte compatible. Changing count/direction or using `均等に戻す` returns to this state.
- Clamp dragged guides against neighboring ratios with a 16 px minimum panel on the Preview axis. For images too small to provide 16 px per panel, use the largest feasible equal minimum so every panel remains non-empty.
- Resolve custom ratios again on each processed output axis, with endpoints fixed at 0 and the full length and sequential integer clamping. This preserves ordering and guarantees no missing, duplicated, or overlapping pixels.
- Batch keeps the existing source-level failure/rollback contract and applies the same ratios independently to each source's post-transform/post-resize dimensions; it does not reuse absolute pixel positions.
