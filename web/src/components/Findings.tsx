import type { Finding, Run } from "../api";
import { CLAIM, FSTATUS, RVERDICT, SEVERITY, avatarClass, letter } from "../model";

export function VerdictBanner({ run }: { run: Run }) {
  const v = run.verdict;
  if (!v?.verdict) return null;
  const [, label] = RVERDICT[v.verdict];
  const confirmed = v.findings.filter((f) => f.status === "confirmed").length;
  const disputed = v.findings.filter((f) => f.status === "disputed").length;
  return (
    <div className={`verdict-banner ${v.verdict}`}>
      <span className="logo" style={{ fontSize: 15 }}>Objection<b>!</b></span>
      <span className="big" style={{ color: `var(--${RVERDICT[v.verdict][0]}T)` }}>{label}</span>
      <span className="muted" style={{ fontSize: 13 }}>
        {confirmed} подтверждено · {disputed} спорно · порог {SEVERITY[run.fail_on]?.[1] ?? run.fail_on}
      </span>
    </div>
  );
}

export function FindingCard({ run, f }: { run: Run; f: Finding }) {
  const [st, sl] = SEVERITY[f.severity] ?? ["n", f.severity];
  const [ft, fl] = FSTATUS[f.status];
  return (
    <div className={`finding ${f.status}`}>
      <div className="hd">
        <b className="muted">{f.id}</b>
        <span className={`tag ${st}`}>{sl}</span>
        <span className={`tag ${ft}`}>{fl}</span>
        {f.evidence && (
          <a className={`tag ${CLAIM[f.evidence]?.[0] ?? "n"}`} href={f.claim_id ? `#claim-${f.claim_id}` : undefined}
            title="Фактчек находки: доказательство главнее голосов"
            onClick={(e) => { if (f.claim_id) { e.preventDefault(); document.getElementById(`claim-${f.claim_id}`)?.scrollIntoView({ behavior: "smooth" }); } }}>
            факт: {CLAIM[f.evidence]?.[1] ?? f.evidence}
          </a>
        )}
        <b>{f.title}</b>
      </div>
      {f.location && <div className="loc">{f.location}</div>}
      {f.detail && <div className="txt" style={{ fontSize: 13 }}>{f.detail}</div>}
      {f.suggestion && <div className="sup"><b>Предложение:</b> {f.suggestion}</div>}
      <div className="votes">
        <span className="muted">нашли:</span>
        {f.reported_by.map((m) => <span key={m} className={`${avatarClass(run.models, m)} xs`} title={m}>{letter(run.models, m)}</span>)}
        <span className="muted" style={{ marginLeft: 8 }}>подтвердили {f.confirmed_by.length} · опровергли {f.refuted_by.length}</span>
      </div>
      {f.refuted_by.map((o, i) => (
        <div key={i} className="obj"><b>Objection! {o.model_id}:</b> {o.reason || "без объяснения"}</div>
      ))}
    </div>
  );
}
