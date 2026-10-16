"use client";

import { MenuItem } from "@/features/ui-foundation/primitives";
import { SplitButton } from "@/features/ui-foundation/SplitButton";
import { useImports } from "./imports/ImportProvider";
import { isActive } from "./imports/import-state";
import local from "./imports/imports.module.css";

export const IMPORT_DISABLED_REASON = "Start the local engine to import captures";

/**
 * The red split Import button. Its main action opens the file picker; the
 * chevron menu offers the recordings folder, the activity drawer and refresh.
 * State lives in `ImportProvider`.
 */
export function ImportSessionButton() {
  const imports = useImports();
  const running = imports.items.filter(isActive).length;
  return (
    <div className={local.importWrap}>
      <SplitButton
        label="Import Session"
        onClick={imports.pickFiles}
        menuLabel="More import options"
        disabled={!imports.enabled}
        ariaDisabled
        disabledReason={IMPORT_DISABLED_REASON}
      >
        <MenuItem onSelect={imports.pickFiles}>Import capture file (.f1ecap)…</MenuItem>
        <MenuItem onSelect={() => imports.openDrawer("folder")}>Import from recordings folder…</MenuItem>
        <MenuItem onSelect={() => imports.openDrawer("recent")}>
          {running > 0 ? `Import activity (${running})` : "Import activity"}
        </MenuItem>
        <hr className={local.menuSeparator} />
        <MenuItem onSelect={imports.refreshLibrary}>Refresh library</MenuItem>
      </SplitButton>
    </div>
  );
}
