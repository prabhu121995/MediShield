import ClaimQueue from "@/components/ClaimQueue";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  return <ClaimQueue name="review" params={await searchParams} />;
}
