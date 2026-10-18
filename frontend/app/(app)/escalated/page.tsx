import ClaimQueue from "@/components/ClaimQueue";

export const dynamic = "force-dynamic";

export default async function EscalatedQueuePage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  return <ClaimQueue name="escalated" params={await searchParams} />;
}
