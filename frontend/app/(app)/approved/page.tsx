import ClaimQueue from "@/components/ClaimQueue";

export const dynamic = "force-dynamic";

export default async function ApprovedQueuePage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  return <ClaimQueue name="approved" params={await searchParams} />;
}
