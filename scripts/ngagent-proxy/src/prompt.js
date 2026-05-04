export function messagesToPrompt(messages) {
  return messages
    .map((message) => {
      const content = Array.isArray(message.content)
        ? message.content
            .map((part) => {
              if (typeof part === "string") return part;
              if (part?.type === "text") return part.text ?? "";
              return "";
            })
            .filter(Boolean)
            .join("\n")
        : String(message.content ?? "");

      return `${message.role}: ${content}`;
    })
    .join("\n\n");
}

export function wrapPrompt(messages) {
  const prompt = messagesToPrompt(messages);
  return [
    "You are serving an OpenAI-compatible chat completion request.",
    "Answer only the latest user request, using the conversation for context.",
    "",
    prompt,
  ].join("\n");
}
