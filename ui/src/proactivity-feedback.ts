export type ProactivityFeedback = "dismiss" | "snooze" | "mute_topic" | "less" | "more";

export async function sendProactivityFeedback(
  baseUrl: string,
  token: string,
  suggestionId: string,
  feedback: ProactivityFeedback,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher(
    `${baseUrl}/v1/proactivity/${encodeURIComponent(suggestionId)}/feedback`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ feedback }),
    },
  );
  if (!response.ok)
    throw new Error(`JARVIS could not save suggestion feedback (${response.status})`);
}
