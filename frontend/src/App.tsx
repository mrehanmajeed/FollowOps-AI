import { useState } from "react";
import { clearToken, getToken, setToken } from "./api/client";
import { Button, Card, Field, inputClass } from "./components/ui";
import { Accounts } from "./pages/Accounts";
import { Integrations } from "./pages/Integrations";
import { Review } from "./pages/Review";
import { Workflows } from "./pages/Workflows";

type Tab = "workflows" | "accounts" | "integrations";

const TABS: { id: Tab; label: string }[] = [
  { id: "workflows", label: "Follow-ups" },
  { id: "accounts", label: "Accounts" },
  { id: "integrations", label: "Integrations" },
];

export default function App() {
  const [token, setTokenState] = useState(getToken());
  const [tab, setTab] = useState<Tab>("workflows");
  const [openWorkflow, setOpenWorkflow] = useState<string | null>(null);

  if (!token) {
    return <SignIn onSignIn={(value) => setTokenState(value)} />;
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-base font-semibold">FollowOps AI</h1>
            <p className="text-xs text-slate-500">
              AI drafts. You approve. The system executes and records.
            </p>
          </div>
          <Button
            onClick={() => {
              clearToken();
              setTokenState("");
            }}
          >
            Sign out
          </Button>
        </div>
        <nav className="mx-auto flex max-w-5xl gap-1 px-4">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              onClick={() => {
                setTab(entry.id);
                setOpenWorkflow(null);
              }}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                tab === entry.id && !openWorkflow
                  ? "border-slate-900 font-medium text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {entry.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-6">
        {openWorkflow ? (
          <Review
            workflowId={openWorkflow}
            onBack={() => setOpenWorkflow(null)}
          />
        ) : tab === "workflows" ? (
          <Workflows onOpen={setOpenWorkflow} />
        ) : tab === "accounts" ? (
          <Accounts />
        ) : (
          <Integrations />
        )}
      </main>
    </div>
  );
}

function SignIn({ onSignIn }: { onSignIn: (token: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <Card title="FollowOps AI — operator sign in">
          <Field
            label="Operator token"
            hint="This is the OPERATOR_API_KEY from the backend .env. It is stored only in this browser."
          >
            <input
              type="password"
              className={inputClass}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && value.trim()) {
                  setToken(value);
                  onSignIn(value.trim());
                }
              }}
            />
          </Field>
          <div className="mt-3">
            <Button
              variant="primary"
              disabled={!value.trim()}
              onClick={() => {
                setToken(value);
                onSignIn(value.trim());
              }}
            >
              Sign in
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
