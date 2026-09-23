# 3D Office

Open **3D Office** in the top bar. Select a company or keep **All workspaces**, then select an occupied desk to inspect the task. The detail panel links to the existing live map, run and approvals, and company.

The scene is original CSS 3D geometry, with no external assets, renderer, CDN or additional service. It reads the existing Studio APIs. It is an overview; it does not add a scheduler or increase worker capacity.

- Each occupied seat represents a recorded task or standalone run. Empty desks are illustrative, not active agents.
- A department shows up to four priority tasks. The list includes every task in the selected company or workspace scope.
- Working and review movement requires recent recorded activity. A running process without fresh events is marked **Awaiting activity**. Independent verification has its own checking state.
- Blocked, paused and cancelled controller states take precedence over leftover process flags. A completed standalone run is **Run finished**, not an accepted product.
- After a connection failure, animation stops, the active count becomes unknown and the view labels the retained snapshot as potentially outdated.
- A stopped execution shows its latest recorded run and model, so a failed reviewer is not confused with the worker profile.
- Department assignment is organizational context, not a permission boundary. The Company Driver still shares one worker slot.

Use the rotate and zoom controls or drag the floor. **Reset camera** restores the initial view. **List view** gives an accessible alternative, including on smaller screens. Reduced-motion preferences disable the scene animations.

Question-mark controls explain the purpose, usage and an example. Click them or use Enter/Space; Escape closes help and returns keyboard focus. Opening help does not submit a form or approve a tool.

The interface and built-in templates are in English. Existing user-authored names, instructions, saved messages and history retain their original language.

## Files and server location

Studio edits files on its host computer. When opened through a LAN address, project paths refer to that server. Both ordinary agent runs and long-running projects map `/workspace` to the project selected in the editor. Run logs and `/outputs` artifacts remain stored separately. Refresh Files after an agent writes a new file; open it to inspect the saved content.
