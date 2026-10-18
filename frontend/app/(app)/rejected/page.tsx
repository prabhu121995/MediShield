import ClaimQueue from "@/components/ClaimQueue";

export const dynamic = "force-dynamic";

export default async function RejectedQueuePage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  return <ClaimQueue name="rejected" params={await searchParams} />;
}
