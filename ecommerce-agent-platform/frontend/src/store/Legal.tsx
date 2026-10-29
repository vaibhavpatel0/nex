// Terms and privacy text for this self-hosted test store, written to what the software actually does.
export function Terms() {
  return (
    <article className="legal">
      <h1>Terms of Service</h1>
      <p className="muted">Applies to this self-hosted installation of the Halden Bazaar test store, version 0.1.0. Last updated 30 October 2026.</p>
      <h2>What this is</h2>
      <p>Halden Bazaar is a fictional store that ships with AEGIS AI + SENTINEL as a realistic target for testing an AI shopping agent. It is not a shop. Products, customers, orders and policies are synthetic. Nothing can be bought, no payment is taken and nothing is delivered. Whoever runs this installation operates it and is responsible for it.</p>
      <h2>Permitted use</h2>
      <ul>
        <li>Use it to develop, test and evaluate AI agents and the SENTINEL policies that govern them.</li>
        <li>Keep it on infrastructure you control. Do not expose it to the public internet as if it were a real store.</li>
        <li>Do not enter real personal data, real payment details or real customer records.</li>
      </ul>
      <h2>The assistant</h2>
      <p>The shopping assistant is a language model. Its answers can be wrong. Store data shown in its answers comes from tool calls, and every tool call is checked by SENTINEL first, but a passing check is not a guarantee that the answer is correct or that the agent is secure.</p>
      <h2>Third-party model providers</h2>
      <p>If the operator configures Ollama cloud models or another hosted provider, conversations and tool results are sent to that provider under its own terms and pricing. The operator is responsible for those costs and terms.</p>
      <h2>Warranty and liability</h2>
      <p>The software is provided as is, without warranty of any kind. To the extent permitted by law, the authors are not liable for damages arising from its use.</p>
    </article>
  );
}

export function Privacy() {
  return (
    <article className="legal">
      <h1>Privacy Policy</h1>
      <p className="muted">Describes what this self-hosted test store stores and sends. Last updated 30 October 2026.</p>
      <h2>Who controls the data</h2>
      <p>The operator of this installation. Data stays in the local database file (by default <code>ecommerce-agent-platform/data/shop.db</code>) and in per-test sandbox files in the same folder.</p>
      <h2>What is stored</h2>
      <ul>
        <li>Accounts: email, display name, role, a salted scrypt password hash. Session tokens are stored only as SHA-256 digests.</li>
        <li>Store records: carts, orders, cancellation and return requests, and support requests, all for synthetic customers.</li>
        <li>Assistant conversations: your messages and the assistant's replies.</li>
        <li>Agent run records: model requests and responses, proposed tool calls, SENTINEL decisions and tool results. Email addresses, phone numbers, addresses and secret-like values are masked before these records are stored.</li>
      </ul>
      <h2>What is sent elsewhere</h2>
      <ul>
        <li>To the configured model (local Ollama by default): the assistant's instructions, recent messages in the conversation, tool definitions and tool results. With an Ollama cloud model this leaves your computer and goes to ollama.com.</li>
        <li>To the AEGIS AI + SENTINEL backend configured by the operator: each proposed tool call, its arguments, the record owner looked up from this store's database, and a preview of the tool result, so SENTINEL can decide and record it.</li>
        <li>No analytics, tracking pixels or third-party scripts. Fonts are bundled.</li>
      </ul>
      <h2>Browser storage</h2>
      <p>Your sign-in token is kept in session storage and cleared when the tab closes.</p>
      <h2>Retention and deletion</h2>
      <p>Records are kept until the operator deletes the database file or re-initialises the store. Sandbox files created by security tests can be deleted at any time.</p>
      <h2>Compliance</h2>
      <p>This test store makes no claim of compliance with any privacy law or framework and must not be used with real personal data.</p>
    </article>
  );
}
