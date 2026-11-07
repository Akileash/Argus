"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { assignDeviceWorker } from "./actions";

export interface WorkerOption {
  id: string;
  name: string;
  crew: string | null;
}

/** Manager-only worker picker for one device row. `options` already excludes workers wearing another hat. */
export function AssignControl({ deviceId, serial, currentWorkerId, options }: { deviceId: string; serial: string; currentWorkerId: string | null; options: WorkerOption[] }) {
  const router = useRouter();
  const [value, setValue] = useState(currentWorkerId ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const changed = value !== (currentWorkerId ?? "");

  function save() {
    setError(null);
    start(async () => {
      const res = await assignDeviceWorker(deviceId, value || null);
      if (res.ok) router.refresh();
      else setError(res.error);
    });
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <select
          aria-label={`Assigned worker for ${serial}`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={pending}
          className="h-8 min-w-0 max-w-[16rem] flex-1 rounded-md border border-line-strong bg-surface px-2 text-sm"
          data-testid={`assign-select-${serial}`}
        >
          <option value="">Unassigned</option>
          {options.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
              {w.crew ? ` (${w.crew})` : ""}
            </option>
          ))}
        </select>
        <Button type="button" size="sm" variant="outline" onClick={save} disabled={!changed || pending} data-testid={`assign-save-${serial}`}>
          {pending ? "Saving..." : "Save"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-xs font-medium text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
