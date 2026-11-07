import { TopBar } from "@/components/shell/top-bar";
import { getSessionContext } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const ctx = await getSessionContext();
  return (
    <>
      <TopBar ctx={ctx} />
      <main className="mx-auto max-w-[1600px] px-4 py-5">{children}</main>
    </>
  );
}
