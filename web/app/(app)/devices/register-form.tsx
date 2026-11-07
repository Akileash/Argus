"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Registered {
  device_id: string;
  device_token: string;
  serial: string;
}

export function RegisterDeviceForm({ orgId }: { orgId: string }) {
  const router = useRouter();
  const [serial, setSerial] = useState("");
  const [rotate, setRotate] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Registered | null>(null);
  const [copied, setCopied] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/devices/register", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ serial: serial.trim(), org_id: orgId, rotate }),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json?.error?.message ?? "Registration failed");
      } else {
        setResult({ ...json, serial: serial.trim() });
        router.refresh();
      }
    } catch {
      setError("Network error");
    } finally {
      setPending(false);
    }
  }

  async function copy() {
    if (!result) return;
    await navigator.clipboard.writeText(result.device_token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (result) {
    return (
      <div className="space-y-3">
        <p className="text-sm">
          Device <span className="num font-semibold">{result.serial}</span> paired. Copy this token into the hat now:{" "}
          <strong>it will not be shown again.</strong>
        </p>
        <div className="flex items-stretch gap-2">
          <code className="num block flex-1 break-all rounded-md border border-line-strong bg-surface-2 px-2.5 py-2 text-xs" data-testid="device-token">
            {result.device_token}
          </code>
          <Button type="button" variant="outline" onClick={copy} aria-label="Copy token">
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <p className="text-xs text-muted">
          On the Pi: set <span className="num">backend.base_url</span> and the token in <span className="num">device/config.yaml</span>, then run{" "}
          <span className="num">argus-cli pair --check</span>.
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            setResult(null);
            setSerial("");
            setRotate(false);
          }}
        >
          Register another
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <div>
        <Label htmlFor="serial">Serial</Label>
        <Input id="serial" value={serial} onChange={(e) => setSerial(e.target.value)} placeholder="ARG-0001" required className="num" />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={rotate} onChange={(e) => setRotate(e.target.checked)} />
        Issue a new token if already paired
      </label>
      {error && (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      )}
      <Button type="submit" disabled={pending || !serial.trim()}>
        {pending ? "Registering..." : "Register device"}
      </Button>
    </form>
  );
}
