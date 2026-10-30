// Terms and privacy text for a self-hosted, local-first prototype. Written to the product's actual scope.
export function LegalTerms() {
  return (
    <article className="legal">
      <h1>Terms of Service</h1>
      <p className="muted">Applies to this self-hosted installation of AEGIS, version 0.1.0. Last updated 29 October 2026.</p>
      <h2>What this software is</h2>
      <p>AEGIS is a prototype for testing AI agents against security scenarios and enforcing tool-call policies at runtime. It is installed and operated by you, on infrastructure you control. Whoever operates the installation is responsible for it; there is no hosted service and no service provider behind this page.</p>
      <h2>Permitted use</h2>
      <ul>
        <li>Test agents and systems that you own or are explicitly authorised to test.</li>
        <li>Use the bundled scenarios and fixtures, which target local sandboxes only.</li>
        <li>Do not point scenarios, integrations or agents at third-party systems without written authorisation from their owners.</li>
        <li>Do not use the platform to develop attacks against systems you are not authorised to test.</li>
      </ul>
      <h2>No security guarantee</h2>
      <p>Test results describe how a specific agent configuration behaved on specific scenarios at a specific time. A passing result does not mean an agent is secure, and SENTINEL only enforces policies on tool calls that go through its supported adapters. Read docs/LIMITATIONS.md before relying on the platform for any decision.</p>
      <h2>Third-party services</h2>
      <p>If you configure Ollama cloud models or a hosted model provider, your prompts and tool results are sent to that provider under its own terms and pricing. You are responsible for any costs and for complying with those terms.</p>
      <h2>Warranty and liability</h2>
      <p>The software is provided as is, without warranty of any kind. To the extent permitted by law, the authors are not liable for damages arising from its use, including missed vulnerabilities, blocked legitimate actions or data loss.</p>
      <h2>Changes</h2>
      <p>These terms ship with the software. A new version of the software may change them; the version and date above identify the text you accepted.</p>
    </article>
  );
}

export function LegalPrivacy() {
  return (
    <article className="legal">
      <h1>Privacy Policy</h1>
      <p className="muted">Describes what this self-hosted installation stores and sends. Last updated 29 October 2026.</p>
      <h2>Who controls the data</h2>
      <p>The operator of this installation. Data stays in the local database file (by default <code>data/aegis.db</code>) and the local sandbox folders unless the operator configures an external model provider.</p>
      <h2>What is stored</h2>
      <ul>
        <li>User accounts: username, role, a salted scrypt password hash, and sign-in times. Session tokens are stored only as SHA-256 digests.</li>
        <li>Agent configurations, permission profiles, policies and their version history, including who changed them.</li>
        <li>Test runs, scenario results, findings, investigations and generated reports.</li>
        <li>Runtime records: tool-call requests, policy decisions, approvals, execution status and timing, and event payloads.</li>
        <li>Agent tasks and final answers, truncated and passed through redaction.</li>
      </ul>
      <h2>Minimisation and redaction</h2>
      <p>Before anything is stored, credentials, card numbers, tax-ID patterns and fixture markers are masked, and fields that tools declare sensitive (for example e-mail bodies) are replaced with a length and short digest. Approved actions keep their original arguments in memory only, until the approval expires or the backend stops. Pattern-based redaction can miss data that does not match a known pattern.</p>
      <h2>What is sent elsewhere</h2>
      <ul>
        <li>Nothing, by default. Simulated agents and fixtures run locally; the web fetch tool serves bundled pages and makes no network requests.</li>
        <li>If you configure Ollama on another host, Ollama cloud models, or a hosted provider, the agent's prompts, tool descriptions and tool results for those agents are sent to that endpoint.</li>
        <li>The interface loads fonts from the local build. It uses no analytics, tracking or third-party scripts.</li>
      </ul>
      <h2>Browser storage</h2>
      <p>Your sign-in token is kept in session storage and cleared when the tab closes. Your theme choice is kept in local storage.</p>
      <h2>Retention and deletion</h2>
      <p>Records are kept until an admin deletes them under Settings, Data and retention, or deletes the database file. Deleting a test run removes its results, traces, findings, investigations and reports.</p>
      <h2>Compliance</h2>
      <p>This prototype makes no claim of compliance with GDPR, HIPAA, SOC 2 or any other framework. Assess it against your own obligations before processing real personal data.</p>
    </article>
  );
}
