"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isLoopbackUrl, pairCommand } from "@/lib/pairing";

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
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [origin, setOrigin] = useState("");
  useEffect(() => setOrigin(window.location.origin), []);

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

  async function copyCommand() {
    if (!result) return;
    await navigator.clipboard.writeText(pairCommand(origin, result.device_token));
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 2000);
  }

  if (result) {
    const command = pairCommand(origin || "<dashboard URL>", result.device_token);
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
        <div className="space-y-2">
          <p className="text-xs text-muted">
            Then, on the Pi, from the <span className="num">device</span> folder, run:
          </p>
          <div className="flex items-stretch gap-2">
            <code className="num block flex-1 break-all rounded-md border border-line-strong bg-surface-2 px-2.5 py-2 text-xs" data-testid="pair-command">
              {command}
            </code>
            <Button type="button" variant="outline" onClick={copyCommand} aria-label="Copy pairing command" data-testid="copy-pair-command">
              {copiedCmd ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              {copiedCmd ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="text-xs text-muted">
            {origin && isLoopbackUrl(origin) ? (
              <strong className="text-incident-fg">
                This dashboard is open at {origin}, which the Pi cannot reach. Open the dashboard at its network address (for example the laptop&apos;s IP) or the hosted URL, then register again.{" "}
              </strong>
            ) : null}
            The dashboard URL must be reachable from the Pi on its network. Afterwards run{" "}
            <span className="num">.venv/bin/argus-cli pair --check --config /etc/argus/config.yaml</span> to confirm.
          </p>
        </div>
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
