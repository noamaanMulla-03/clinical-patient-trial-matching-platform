import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

import { apiPath, requestJson } from './api';
import {
  displayDate,
  displayFactValue,
  factFreshness,
  Fact,
  filterMatchCandidates,
  ImportResult,
  CriterionDetail,
  CriterionOutcome,
  MatchCandidate,
  MatchRun,
  ResultTab,
  buildBoundedTrialSyncRequest,
  Timeline,
  TrialCatalogueStatus,
  TrialSync,
  TrialSyncCreateInput,
  retrievalReason,
} from './clinical';

type Screen = 'catalogue' | 'import' | 'timeline' | 'match-run' | 'criterion-detail';

const screens: { id: Screen; label: string; description: string }[] = [
  {
    id: 'catalogue',
    label: 'Trial catalogue',
    description: 'Public trial setup',
  },
  { id: 'import', label: 'Patient import', description: 'Synthetic FHIR R4 only' },
  { id: 'timeline', label: 'Patient timeline', description: 'Source-linked facts' },
  { id: 'match-run', label: 'Match-run status', description: 'Operational review' },
  {
    id: 'criterion-detail',
    label: 'Criterion detail',
    description: 'Evidence and reviewer corrections',
  },
];

function App() {
  const [screen, setScreen] = useState<Screen>('catalogue');
  const [patientId, setPatientId] = useState('');
  const [patientImportId, setPatientImportId] = useState('');
  const [criterionResultId, setCriterionResultId] = useState('');

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Research-only · synthetic data</p>
          <h1>Trial Match Review</h1>
          <p className="lede">
            Import, inspect, and monitor evidence without making enrollment decisions.
          </p>
        </div>
        <p className="safety-note">
          Candidate retrieval supports human review only. It is not an eligibility
          determination.
        </p>
      </header>

      <nav aria-label="Workflow screens" className="workflow-nav">
        {screens.map((item) => (
          <button
            className={screen === item.id ? 'nav-item active' : 'nav-item'}
            key={item.id}
            onClick={() => setScreen(item.id)}
            type="button"
          >
            <span>{item.label}</span>
            <small>{item.description}</small>
          </button>
        ))}
      </nav>

      {screen === 'catalogue' && <CatalogueScreen />}
      {screen === 'import' && (
        <ImportScreen
          onImported={(result) => {
            setPatientId(result.patient_id);
            setPatientImportId(result.patient_import_id);
            setScreen('timeline');
          }}
        />
      )}
      {screen === 'timeline' && (
        <TimelineScreen
          patientId={patientId}
          onPatientIdChange={setPatientId}
          onUseImport={(id) => {
            setPatientImportId(id);
            setScreen('match-run');
          }}
        />
      )}
      {screen === 'match-run' && (
        <MatchRunScreen
          onOpenCatalogue={() => setScreen('catalogue')}
          patientImportId={patientImportId}
          onPatientImportIdChange={setPatientImportId}
          onOpenCriterion={(id) => {
            setCriterionResultId(id);
            setScreen('criterion-detail');
          }}
        />
      )}
      {screen === 'criterion-detail' && (
        <CriterionDetailScreen
          selectedResultId={criterionResultId}
          onSelectedResultIdChange={setCriterionResultId}
        />
      )}
    </main>
  );
}

const blankTrialSyncInput: TrialSyncCreateInput = {
  nctId: '',
  queryTerm: '',
  condition: '',
  startPage: '',
  endPage: '',
  pageSize: '25',
};

function CatalogueScreen() {
  const [catalogue, setCatalogue] = useState<TrialCatalogueStatus>();
  const [syncs, setSyncs] = useState<TrialSync[]>([]);
  const [selection, setSelection] = useState<TrialSyncCreateInput>(blankTrialSyncInput);
  const [message, setMessage] = useState<string>();
  const [refreshNotice, setRefreshNotice] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const activeSyncIds = syncs
    .filter((sync) => sync.status === 'queued' || sync.status === 'running')
    .map((sync) => sync.id);

  const refreshCatalogue = useCallback(async () => {
    setRefreshing(true);
    try {
      const current = await requestJson<TrialCatalogueStatus>('/trial-catalogue');
      setCatalogue(current);
      setRefreshNotice(
        `Catalogue refreshed: ${current.searchable_trial_count} public trial record${current.searchable_trial_count === 1 ? '' : 's'} available.`,
      );
      const latestSync = current.latest_sync;
      if (latestSync)
        setSyncs((previous) =>
          previous.some((sync) => sync.id === latestSync.id)
            ? previous
            : [latestSync, ...previous],
        );
    } catch {
      setMessage('The trial catalogue status could not be loaded.');
    } finally {
      setRefreshing(false);
    }
  }, []);

  const refreshSyncs = useCallback(
    async (syncIds: string[]) => {
      try {
        const updates = await Promise.all(
          syncIds.map((id) =>
            requestJson<TrialSync>(`/trial-syncs/${encodeURIComponent(id)}`),
          ),
        );
        setSyncs((previous) =>
          previous.map(
            (sync) => updates.find((update) => update.id === sync.id) ?? sync,
          ),
        );
        await refreshCatalogue();
      } catch {
        setMessage('The trial update status could not be refreshed.');
      }
    },
    [refreshCatalogue],
  );

  useEffect(() => {
    void refreshCatalogue();
  }, [refreshCatalogue]);
  useEffect(() => {
    if (activeSyncIds.length === 0) return;
    const interval = window.setInterval(() => void refreshSyncs(activeSyncIds), 3_000);
    return () => window.clearInterval(interval);
  }, [activeSyncIds, refreshSyncs]);

  async function queueDemoCollection() {
    setBusy(true);
    setMessage(undefined);
    try {
      const queued = await requestJson<TrialSync[]>(
        '/trial-syncs/development-collection',
        { method: 'POST' },
      );
      setSyncs(queued);
      await refreshCatalogue();
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : 'The fixed demo trial collection could not be queued.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function queueAdvancedSelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(undefined);
    let request;
    try {
      request = buildBoundedTrialSyncRequest(selection);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Check the trial selection.');
      return;
    }
    setBusy(true);
    try {
      const queued = await requestJson<TrialSync>('/trial-syncs', {
        method: 'POST',
        body: JSON.stringify(request),
      });
      setSyncs([queued]);
      await refreshCatalogue();
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : 'The selected trial update could not be queued.',
      );
    } finally {
      setBusy(false);
    }
  }

  const displayedSyncs = useMemo(() => {
    const latest = catalogue?.latest_sync;
    return latest && !syncs.some((sync) => sync.id === latest.id)
      ? [latest, ...syncs]
      : syncs;
  }, [catalogue?.latest_sync, syncs]);

  return (
    <section aria-labelledby="catalogue-title">
      <div className="screen-heading">
        <div>
          <p className="eyebrow">Step 0 · Local demo administration</p>
          <h2 id="catalogue-title">Trial catalogue</h2>
          <p>
            Load bounded public ClinicalTrials.gov records for this local research
            demonstration. These controls do not use patient information.
          </p>
        </div>
        <button
          className="button secondary"
          disabled={refreshing}
          onClick={() => void refreshCatalogue()}
          type="button"
        >
          {refreshing ? 'Refreshing…' : 'Refresh status'}
        </button>
      </div>
      <p className="safety-note inline">
        Trial retrieval creates review candidates only. It does not determine
        eligibility, treatment, enrollment, or outreach.
      </p>
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
      {catalogue ? (
        <CatalogueStatusCard catalogue={catalogue} />
      ) : (
        <p className="muted">Loading catalogue status…</p>
      )}
      {refreshNotice && <p className="catalogue-refresh-notice">{refreshNotice}</p>}
      <div className="catalogue-actions">
        <article className="panel">
          <p className="eyebrow">Local demo collection</p>
          <h3>Small, reproducible public trial set</h3>
          <p>
            Queue the fixed source-controlled collection used to check the review
            workflow. It contains no patient information.
          </p>
          <button
            className="button"
            disabled={busy}
            onClick={() => void queueDemoCollection()}
            type="button"
          >
            {busy ? 'Queuing…' : 'Load demo trials'}
          </button>
        </article>
        <details className="panel advanced-selection">
          <summary>Advanced bounded update</summary>
          <p>
            Choose one public source selection. These fields control trial retrieval,
            not patient matching.
          </p>
          <form onSubmit={queueAdvancedSelection}>
            <div className="catalogue-form-grid">
              <label htmlFor="trial-nct-id">
                Specific NCT ID
                <input
                  id="trial-nct-id"
                  onChange={(event) =>
                    setSelection({ ...selection, nctId: event.target.value })
                  }
                  placeholder="NCT02434107"
                  value={selection.nctId}
                />
              </label>
              <label htmlFor="trial-condition">
                Condition
                <input
                  id="trial-condition"
                  onChange={(event) =>
                    setSelection({ ...selection, condition: event.target.value })
                  }
                  placeholder="e.g. melanoma"
                  value={selection.condition}
                />
              </label>
              <label htmlFor="trial-search">
                Search phrase
                <input
                  id="trial-search"
                  onChange={(event) =>
                    setSelection({ ...selection, queryTerm: event.target.value })
                  }
                  placeholder="e.g. immunotherapy"
                  value={selection.queryTerm}
                />
              </label>
              <label htmlFor="trial-page-size">
                Page size
                <input
                  id="trial-page-size"
                  min="1"
                  max="1000"
                  onChange={(event) =>
                    setSelection({ ...selection, pageSize: event.target.value })
                  }
                  type="number"
                  value={selection.pageSize}
                />
              </label>
              <label htmlFor="trial-start-page">
                Start page
                <input
                  id="trial-start-page"
                  min="1"
                  onChange={(event) =>
                    setSelection({ ...selection, startPage: event.target.value })
                  }
                  type="number"
                  value={selection.startPage}
                />
              </label>
              <label htmlFor="trial-end-page">
                End page
                <input
                  id="trial-end-page"
                  min="1"
                  onChange={(event) =>
                    setSelection({ ...selection, endPage: event.target.value })
                  }
                  type="number"
                  value={selection.endPage}
                />
              </label>
            </div>
            <button className="button" disabled={busy} type="submit">
              {busy ? 'Queuing…' : 'Queue trial update'}
            </button>
          </form>
        </details>
      </div>
      {displayedSyncs.length > 0 && (
        <section className="sync-list" aria-labelledby="latest-update-title">
          <h3 id="latest-update-title">Latest update</h3>
          <p className="muted">
            This card shows the most recent update only. The catalogue currently
            contains {catalogue?.searchable_trial_count ?? 0} public trial record
            {(catalogue?.searchable_trial_count ?? 0) === 1 ? '' : 's'}.
          </p>
          {displayedSyncs.map((sync) => (
            <TrialSyncCard
              key={sync.id}
              onRefresh={() => void refreshSyncs([sync.id])}
              sync={sync}
            />
          ))}
        </section>
      )}
    </section>
  );
}

function CatalogueStatusCard({ catalogue }: { catalogue: TrialCatalogueStatus }) {
  const stateText =
    catalogue.state === 'empty'
      ? 'Catalogue empty'
      : catalogue.state === 'updating'
        ? 'Update in progress'
        : 'Catalogue ready';
  return (
    <article className="catalogue-status-card">
      <div>
        <span
          className={`status ${catalogue.state === 'ready' ? 'completed' : catalogue.state === 'updating' ? 'running' : 'queued'}`}
        >
          {stateText}
        </span>
        <strong>
          {catalogue.searchable_trial_count} current searchable public trial records
        </strong>
      </div>
      <dl>
        <div>
          <dt>Latest successful update</dt>
          <dd>{displayDate(catalogue.latest_successful_update_at)}</dd>
        </div>
        <div>
          <dt>Source update dates</dt>
          <dd>
            {catalogue.freshness.records_with_source_update_time} recorded ·{' '}
            {catalogue.freshness.records_missing_source_update_time} not recorded
          </dd>
        </div>
        <div>
          <dt>Most recent source update</dt>
          <dd>{displayDate(catalogue.freshness.newest_source_update_at)}</dd>
        </div>
        <div>
          <dt>Last retrieved</dt>
          <dd>{displayDate(catalogue.freshness.latest_retrieved_at)}</dd>
        </div>
      </dl>
      {catalogue.state === 'empty' && (
        <p className="quality-banner">
          No trials are loaded yet. Load a bounded public trial collection before
          running patient matches.
        </p>
      )}
    </article>
  );
}

function TrialSyncCard({
  sync,
  onRefresh,
}: {
  sync: TrialSync;
  onRefresh: () => void;
}) {
  const statusText =
    sync.status === 'queued'
      ? 'Waiting to start'
      : sync.status === 'running'
        ? 'Loading public trial records'
        : sync.status === 'completed'
          ? 'Update completed'
          : 'Update could not be completed';
  return (
    <article className="run-details trial-sync-card">
      <div className="run-status">
        <div>
          <p className="eyebrow">Public trial update</p>
          <h4>
            <span className={`status ${sync.status}`}>{statusText}</span>
          </h4>
          <p>{trialSyncSelectionLabel(sync.selection)}</p>
        </div>
        <button className="button secondary" onClick={onRefresh} type="button">
          Refresh
        </button>
      </div>
      <div className="run-grid">
        <dl>
          <div>
            <dt>Queued</dt>
            <dd>{displayDate(sync.created_at)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{displayDate(sync.started_at)}</dd>
          </div>
          <div>
            <dt>Completed</dt>
            <dd>{displayDate(sync.completed_at)}</dd>
          </div>
        </dl>
        <dl>
          <div>
            <dt>Trials processed</dt>
            <dd>{sync.counts.studies_processed}</dd>
          </div>
          <div>
            <dt>New versions stored</dt>
            <dd>{sync.counts.versions_created}</dd>
          </div>
          <div>
            <dt>Unchanged records</dt>
            <dd>{sync.counts.unchanged_studies}</dd>
          </div>
          <div>
            <dt>Source update dates</dt>
            <dd>
              {sync.source_lag.records_with_update_time} recorded ·{' '}
              {sync.source_lag.records_missing_update_time} missing ·{' '}
              {sync.source_lag.records_invalid_update_time} invalid
            </dd>
          </div>
        </dl>
      </div>
      {sync.failure && <p className="error">{sync.failure.message}</p>}
    </article>
  );
}

function trialSyncSelectionLabel(selection: TrialSync['selection']): string {
  if (selection.collection_id) return 'Fixed local development collection';
  if (selection.nct_id) return `Specific NCT ID: ${selection.nct_id}`;
  if (selection.condition) return `Condition: ${selection.condition}`;
  if (selection.query_term) return `Search phrase: ${selection.query_term}`;
  return `Explicit pages ${selection.start_page} to ${selection.end_page}`;
}

function ImportScreen({ onImported }: { onImported: (result: ImportResult) => void }) {
  const [bundleText, setBundleText] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(undefined);
    let bundle: unknown;
    try {
      bundle = JSON.parse(bundleText);
    } catch {
      setMessage('Enter a valid JSON FHIR Bundle before importing.');
      return;
    }
    setBusy(true);
    try {
      const result = await requestJson<ImportResult>('/patients/import/fhir', {
        method: 'POST',
        body: JSON.stringify({ bundle }),
      });
      onImported(result);
    } catch {
      setMessage(
        'The synthetic FHIR Bundle could not be imported. Confirm its synthetic marker and Patient resource.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="screen-grid import-screen" aria-labelledby="import-title">
      <div className="panel primary-panel">
        <p className="eyebrow">Step 1</p>
        <h2 id="import-title">Import a synthetic patient Bundle</h2>
        <p>
          Paste one complete synthetically marked FHIR R4 Bundle. The import is stored
          as an immutable source snapshot and normalized facts remain traceable to it.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="fhir-bundle">FHIR Bundle JSON</label>
          <textarea
            id="fhir-bundle"
            value={bundleText}
            onChange={(event) => setBundleText(event.target.value)}
            placeholder={
              '{\n  "resourceType": "Bundle",\n  "meta": { "tag": [ ...synthetic marker... ] },\n  "entry": [ ... ]\n}'
            }
            spellCheck={false}
            required
          />
          {message && (
            <p className="error" role="alert">
              {message}
            </p>
          )}
          <button className="button" disabled={busy} type="submit">
            {busy ? 'Importing…' : 'Import synthetic Bundle'}
          </button>
        </form>
      </div>
      <aside className="panel guidance">
        <h2>What is retained</h2>
        <ul>
          <li>FHIR resource provenance and version</li>
          <li>Source dates, precision, units, and quality flags</li>
          <li>An immutable import snapshot for later review</li>
        </ul>
        <p>Only synthetic data is accepted by this build.</p>
      </aside>
    </section>
  );
}

function TimelineScreen({
  patientId,
  onPatientIdChange,
  onUseImport,
}: {
  patientId: string;
  onPatientIdChange: (value: string) => void;
  onUseImport: (value: string) => void;
}) {
  const [requestedPatientId, setRequestedPatientId] = useState(patientId);
  const [timeline, setTimeline] = useState<Timeline>();
  const [message, setMessage] = useState<string>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setRequestedPatientId(patientId);
  }, [patientId]);
  useEffect(() => {
    if (patientId) void load(patientId);
  }, [patientId]);

  async function load(id: string) {
    setLoading(true);
    setMessage(undefined);
    try {
      setTimeline(await requestJson<Timeline>(`/patients/${encodeURIComponent(id)}`));
    } catch {
      setTimeline(undefined);
      setMessage('The synthetic patient timeline could not be found.');
    } finally {
      setLoading(false);
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = requestedPatientId.trim();
    onPatientIdChange(id);
    if (id) void load(id);
  }

  return (
    <section aria-labelledby="timeline-title">
      <div className="screen-heading">
        <div>
          <p className="eyebrow">Step 2</p>
          <h2 id="timeline-title">Patient timeline</h2>
          <p>
            Facts are shown from one latest completed import; they are never silently
            merged across source snapshots.
          </p>
        </div>
        <form className="lookup" onSubmit={submit}>
          <label htmlFor="patient-id">Synthetic patient ID</label>
          <div>
            <input
              id="patient-id"
              value={requestedPatientId}
              onChange={(event) => setRequestedPatientId(event.target.value)}
              placeholder="e.g. synthea-001"
              required
            />
            <button className="button secondary" type="submit">
              Load
            </button>
          </div>
        </form>
      </div>
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
      {loading && <p className="muted">Loading source-linked facts…</p>}
      {timeline && <TimelineDetails timeline={timeline} onUseImport={onUseImport} />}
      {!loading && !timeline && !message && (
        <EmptyState text="Import a synthetic patient Bundle or enter a synthetic patient ID to inspect its timeline." />
      )}
    </section>
  );
}

function TimelineDetails({
  timeline,
  onUseImport,
}: {
  timeline: Timeline;
  onUseImport: (value: string) => void;
}) {
  if (!timeline.import_snapshot)
    return (
      <EmptyState text="This synthetic patient has no completed import snapshot." />
    );
  const snapshot = timeline.import_snapshot;
  return (
    <>
      <div className="snapshot-bar">
        <div>
          <span className="status complete">Completed import</span>
          <strong>{timeline.facts.length} source-linked facts</strong>
        </div>
        <dl>
          <div>
            <dt>Imported</dt>
            <dd>{displayDate(snapshot.completed_at ?? snapshot.created_at)}</dd>
          </div>
          <div>
            <dt>FHIR version</dt>
            <dd>{snapshot.fhir_version}</dd>
          </div>
          <div>
            <dt>Snapshot</dt>
            <dd className="mono">{snapshot.source_hash.slice(0, 12)}…</dd>
          </div>
        </dl>
        <button
          className="button"
          type="button"
          onClick={() => onUseImport(snapshot.id)}
        >
          Use for match run
        </button>
      </div>
      {snapshot.data_quality_issues.length > 0 && (
        <p className="quality-banner">
          Import has {snapshot.data_quality_issues.length} recorded data-quality
          flag(s). Review facts before relying on them.
        </p>
      )}
      <div className="timeline">
        {timeline.facts.map((fact) => (
          <FactCard fact={fact} patientId={timeline.patient_id} key={fact.fact_id} />
        ))}
      </div>
    </>
  );
}

function FactCard({ fact, patientId }: { fact: Fact; patientId: string }) {
  const sourceLabel = `${fact.source.resource_type}/${fact.source.resource_id}${fact.source.version_id ? ` · v${fact.source.version_id}` : ''}`;
  return (
    <article className="fact-card">
      <div className="timeline-dot" />
      <div className="fact-content">
        <div className="fact-heading">
          <div>
            <p className="eyebrow">{fact.kind}</p>
            <h3>{fact.code.display ?? fact.code.value}</h3>
            <p className="code">
              {fact.code.system} · {fact.code.value}
            </p>
          </div>
          <span className="freshness">{factFreshness(fact)}</span>
        </div>
        <p className="fact-value">{displayFactValue(fact)}</p>
        <dl className="fact-meta">
          <div>
            <dt>Recorded date</dt>
            <dd>{displayDate(fact.effective_at)}</dd>
          </div>
          <div>
            <dt>Source resource</dt>
            <dd>
              <a
                href={apiPath(
                  `/patients/${encodeURIComponent(patientId)}/facts/${encodeURIComponent(fact.fact_id)}/source`,
                )}
                target="_blank"
                rel="noreferrer"
              >
                {sourceLabel}
              </a>
            </dd>
          </div>
        </dl>
        {fact.quality_issues.length > 0 && (
          <ul className="issue-list">
            {fact.quality_issues.map((issue) => (
              <li key={`${issue.code}-${issue.field}`}>
                <strong>{issue.code}</strong>: {issue.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}

function MatchRunScreen({
  onOpenCatalogue,
  patientImportId,
  onPatientImportIdChange,
  onOpenCriterion,
}: {
  onOpenCatalogue: () => void;
  patientImportId: string;
  onPatientImportIdChange: (value: string) => void;
  onOpenCriterion: (id: string) => void;
}) {
  const [requestedImportId, setRequestedImportId] = useState(patientImportId);
  const [run, setRun] = useState<MatchRun>();
  const [candidates, setCandidates] = useState<MatchCandidate[]>([]);
  const [catalogue, setCatalogue] = useState<TrialCatalogueStatus>();
  const [message, setMessage] = useState<string>();
  const [busy, setBusy] = useState(false);
  const activeRunId = run?.id;
  const activeRunStatus = run?.status;
  useEffect(() => {
    setRequestedImportId(patientImportId);
  }, [patientImportId]);
  useEffect(() => {
    void requestJson<TrialCatalogueStatus>('/trial-catalogue')
      .then(setCatalogue)
      .catch(() => setMessage('The trial catalogue status could not be loaded.'));
  }, []);
  const refresh = useCallback(async (runId: string) => {
    try {
      const current = await requestJson<MatchRun>(
        `/match-runs/${encodeURIComponent(runId)}`,
      );
      setRun(current);
      if (current.status === 'completed')
        setCandidates(
          await requestJson<MatchCandidate[]>(
            `/match-runs/${encodeURIComponent(runId)}/results`,
          ),
        );
    } catch {
      setMessage('The match-run status could not be refreshed.');
    }
  }, []);
  useEffect(() => {
    if (
      !activeRunId ||
      !activeRunStatus ||
      !['queued', 'running'].includes(activeRunStatus)
    )
      return;
    const interval = window.setInterval(() => void refresh(activeRunId), 3_000);
    return () => window.clearInterval(interval);
  }, [activeRunId, activeRunStatus, refresh]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = requestedImportId.trim();
    if (!id) return;
    if (!catalogue) {
      setMessage('Wait for the trial catalogue status before queuing a match run.');
      return;
    }
    if (catalogue.state === 'empty') {
      setMessage(
        'The trial catalogue is empty. Load public trial records before matching.',
      );
      return;
    }
    setBusy(true);
    setMessage(undefined);
    setCandidates([]);
    try {
      const created = await requestJson<MatchRun>('/match-runs', {
        method: 'POST',
        body: JSON.stringify({ patient_import_id: id }),
      });
      onPatientImportIdChange(id);
      setRun(created);
    } catch {
      setMessage(
        'A match run could not be queued for that completed synthetic patient import.',
      );
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!run) return;
    setBusy(true);
    try {
      setRun(
        await requestJson<MatchRun>(
          `/match-runs/${encodeURIComponent(run.id)}/cancel`,
          { method: 'POST' },
        ),
      );
    } catch {
      setMessage('This match run could not be cancelled.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="match-title">
      <div className="screen-heading">
        <div>
          <p className="eyebrow">Step 3</p>
          <h2 id="match-title">Match-run status</h2>
          <p>Queue lexical candidate retrieval, then monitor durable worker status.</p>
        </div>
        <form className="lookup" onSubmit={create}>
          <label htmlFor="import-id">Completed patient import ID</label>
          <div>
            <input
              id="import-id"
              value={requestedImportId}
              onChange={(event) => setRequestedImportId(event.target.value)}
              placeholder="UUID from import"
              required
            />
            <button
              className="button"
              disabled={busy || !catalogue || catalogue.state === 'empty'}
              type="submit"
            >
              Queue run
            </button>
          </div>
        </form>
      </div>
      <p className="safety-note inline">
        Results are review candidates only; no eligibility, treatment, or enrollment
        decision is made here.
      </p>
      {catalogue?.state === 'empty' && (
        <p className="quality-banner catalogue-empty-notice">
          Trial catalogue is empty. Matching would return no results.{' '}
          <button onClick={onOpenCatalogue} type="button">
            Open Trial catalogue
          </button>
        </p>
      )}
      {catalogue?.state === 'ready' && (
        <p className="catalogue-ready-notice">
          Trial catalogue ready: {catalogue.searchable_trial_count} public trial record
          {catalogue.searchable_trial_count === 1 ? '' : 's'} available.
        </p>
      )}
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
      {run && (
        <RunDetails
          run={run}
          candidates={candidates}
          busy={busy}
          onCancel={cancel}
          onRefresh={() => void refresh(run.id)}
          onOpenCriterion={onOpenCriterion}
        />
      )}
    </section>
  );
}

function RunDetails({
  run,
  candidates,
  busy,
  onCancel,
  onRefresh,
  onOpenCriterion,
}: {
  run: MatchRun;
  candidates: MatchCandidate[];
  busy: boolean;
  onCancel: () => void;
  onRefresh: () => void;
  onOpenCriterion: (id: string) => void;
}) {
  const canCancel = run.status === 'queued' || run.status === 'running';
  const statusText = useMemo(() => run.status.replace('_', ' '), [run.status]);
  return (
    <div className="run-details">
      <div className="run-status">
        <div>
          <p className="eyebrow">
            Run <span className="mono">{run.id}</span>
          </p>
          <h3>
            <span className={`status ${run.status}`}>{statusText}</span>
          </h3>
          <p>
            {run.candidate_count} of {run.candidate_limit} candidate slots populated
          </p>
        </div>
        <div className="action-row">
          <button className="button secondary" onClick={onRefresh} type="button">
            Refresh
          </button>
          {canCancel && (
            <button
              className="button danger"
              disabled={busy}
              onClick={onCancel}
              type="button"
            >
              Cancel run
            </button>
          )}
        </div>
      </div>
      <div className="run-grid">
        <dl>
          <div>
            <dt>Queued</dt>
            <dd>{displayDate(run.created_at)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{displayDate(run.started_at)}</dd>
          </div>
          <div>
            <dt>Completed</dt>
            <dd>{displayDate(run.completed_at)}</dd>
          </div>
        </dl>
        <dl>
          {Object.entries(run.configuration_versions).map(([name, version]) => (
            <div key={name}>
              <dt>{name.replace(/_/g, ' ')}</dt>
              <dd className="mono">{version}</dd>
            </div>
          ))}
        </dl>
      </div>
      {run.failure && <p className="error">{run.failure.message}</p>}
      {run.cancellation_requested && (
        <p className="quality-banner">
          Cancellation has been requested; the worker will stop at its next safe
          checkpoint.
        </p>
      )}
      {run.status === 'completed' && (
        <CandidateList candidates={candidates} onOpenCriterion={onOpenCriterion} />
      )}
    </div>
  );
}

function CandidateList({
  candidates,
  onOpenCriterion,
}: {
  candidates: MatchCandidate[];
  onOpenCriterion: (id: string) => void;
}) {
  const [tab, setTab] = useState<ResultTab>('all');
  const [search, setSearch] = useState('');
  const [studyStatus, setStudyStatus] = useState('all');
  const studyStatuses = useMemo(
    () =>
      Array.from(
        new Set(
          candidates
            .map((candidate) => candidate.study_status)
            .filter((status): status is string => Boolean(status)),
        ),
      ).sort(),
    [candidates],
  );
  const visibleCandidates = useMemo(
    () => filterMatchCandidates(candidates, tab, search, studyStatus),
    [candidates, search, studyStatus, tab],
  );
  const tabs: { id: ResultTab; label: string }[] = [
    { id: 'all', label: 'All results' },
    { id: 'potential_matches', label: 'Potential matches' },
    { id: 'needs_review', label: 'Needs review' },
    { id: 'likely_exclusions', label: 'Likely exclusions' },
  ];

  return (
    <section className="candidate-list" aria-labelledby="candidate-title">
      <h3 id="candidate-title">Retrieved candidates</h3>
      {candidates.length === 0 ? (
        <p className="muted">No candidates were retrieved by this run.</p>
      ) : (
        <>
          <div className="result-tabs" role="tablist" aria-label="Candidate outcomes">
            {tabs.map((item) => (
              <button
                aria-selected={tab === item.id}
                className={tab === item.id ? 'result-tab active' : 'result-tab'}
                key={item.id}
                onClick={() => setTab(item.id)}
                role="tab"
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="result-filters">
            <label htmlFor="candidate-search">Search results</label>
            <input
              id="candidate-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Trial title, NCT ID, or study status"
              type="search"
            />
            <label htmlFor="study-status">Study status</label>
            <select
              id="study-status"
              value={studyStatus}
              onChange={(event) => setStudyStatus(event.target.value)}
            >
              <option value="all">All statuses</option>
              {studyStatuses.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
              <option value="not_recorded">Not recorded</option>
            </select>
          </div>
          {visibleCandidates.length === 0 && (
            <p className="muted">No results match this tab and filter.</p>
          )}
          <ol aria-label="Filtered retrieved candidates">
            {visibleCandidates.map((candidate) => (
              <li key={candidate.id}>
                <span className="rank">{candidate.candidate_rank}</span>
                <div className="candidate-summary">
                  <a
                    href={`https://clinicaltrials.gov/study/${encodeURIComponent(candidate.nct_id)}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {candidate.nct_id}
                  </a>
                  <strong>{candidate.title ?? 'Untitled trial'}</strong>
                  <dl className="candidate-metadata">
                    <div>
                      <dt>Study status</dt>
                      <dd>{candidate.study_status ?? 'Not recorded'}</dd>
                    </div>
                    <div>
                      <dt>Source updated</dt>
                      <dd>{displayDate(candidate.source_updated_at)}</dd>
                    </div>
                    <div>
                      <dt>Retrieval relevance</dt>
                      <dd>
                        {candidate.retrieval_relevance
                          ? `${candidate.retrieval_relevance.score.toFixed(1)} score · ${candidate.retrieval_relevance.matched_term_count}/${candidate.retrieval_relevance.query_term_count} terms`
                          : 'Not available'}
                      </dd>
                    </div>
                    <div className="why-listed">
                      <dt>Why listed</dt>
                      <dd>{retrievalReason(candidate.retrieval_relevance)}</dd>
                    </div>
                  </dl>
                  {candidate.retrieval_relevance?.matched_fact_ids.length ? (
                    <div className="matched-facts">
                      <span>Matched source facts</span>
                      {candidate.retrieval_relevance.matched_fact_ids.map((factId) => (
                        <a
                          href={apiPath(
                            `/patients/${encodeURIComponent(candidate.patient_id)}/facts/${encodeURIComponent(factId)}/source`,
                          )}
                          key={factId}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {factId}
                        </a>
                      ))}
                    </div>
                  ) : null}
                  {candidate.criterion_results.length > 0 ? (
                    <div className="criterion-links">
                      <span>Outcome evidence</span>
                      {candidate.criterion_results.map((criterion) => (
                        <button
                          key={criterion.id}
                          onClick={() => onOpenCriterion(criterion.id)}
                          type="button"
                        >
                          Review {criterion.category} criterion: {criterion.source_text}
                          {' · '}
                          {criterion.current_outcome.replace('_', ' ')}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="muted criterion-unavailable">
                      No criterion evaluation is available for this retrieved candidate.
                    </p>
                  )}
                </div>
                <span className="outcome" aria-label="Review outcome">
                  {candidate.outcome?.replace('_', ' ') ?? 'Not yet reviewed'}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function CriterionDetailScreen({
  selectedResultId,
  onSelectedResultIdChange,
}: {
  selectedResultId: string;
  onSelectedResultIdChange: (id: string) => void;
}) {
  const [resultId, setResultId] = useState(selectedResultId);
  const [detail, setDetail] = useState<CriterionDetail>();
  const [message, setMessage] = useState<string>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setResultId(selectedResultId);
    if (selectedResultId) void load(selectedResultId);
  }, [selectedResultId]);

  async function load(resultIdToLoad: string) {
    setLoading(true);
    setMessage(undefined);
    try {
      setDetail(
        await requestJson<CriterionDetail>(
          `/criterion-results/${encodeURIComponent(resultIdToLoad)}`,
        ),
      );
    } catch {
      setDetail(undefined);
      setMessage('The criterion result could not be found or safely displayed.');
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = resultId.trim();
    if (id) {
      onSelectedResultIdChange(id);
      void load(id);
    }
  }

  return (
    <section aria-labelledby="criterion-detail-title">
      <div className="screen-heading">
        <div>
          <p className="eyebrow">Step 4</p>
          <h2 id="criterion-detail-title">Criterion detail</h2>
          <p>
            Review source text, deterministic evaluation, snapshot evidence, and any
            append-only reviewer corrections.
          </p>
        </div>
        <form className="lookup" onSubmit={submit}>
          <label htmlFor="criterion-result-id">Criterion result ID</label>
          <div>
            <input
              id="criterion-result-id"
              value={resultId}
              onChange={(event) => setResultId(event.target.value)}
              placeholder="UUID from a criterion result"
              required
            />
            <button className="button secondary" disabled={loading} type="submit">
              Load
            </button>
          </div>
        </form>
      </div>
      <p className="safety-note inline">
        Reviewer corrections preserve the original deterministic outcome and do not make
        an enrollment decision.
      </p>
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
      {loading && <p className="muted">Loading source-linked criterion detail…</p>}
      {detail && (
        <CriterionDetailView
          detail={detail}
          onCorrected={() => void load(detail.evaluation.id)}
        />
      )}
      {!loading && !detail && !message && (
        <EmptyState text="Enter a criterion result ID to inspect its immutable evidence and correction history." />
      )}
    </section>
  );
}

function CriterionDetailView({
  detail,
  onCorrected,
}: {
  detail: CriterionDetail;
  onCorrected: () => void;
}) {
  const outcomeTone = criterionOutcomeTone(
    detail.criterion.category,
    detail.evaluation.current_outcome,
  );
  return (
    <div className="criterion-detail">
      <section className={`criterion-status ${outcomeTone}`}>
        <div>
          <p className="eyebrow">Current review state</p>
          <h3>{detail.evaluation.current_outcome.replace('_', ' ')}</h3>
          <p>
            Original deterministic outcome: <strong>{detail.evaluation.outcome}</strong>
          </p>
        </div>
        <dl>
          <div>
            <dt>Evaluation path</dt>
            <dd>{detail.evaluation.evaluation_path}</dd>
          </div>
          <div>
            <dt>Evaluated</dt>
            <dd>{displayDate(detail.evaluation.evaluated_at)}</dd>
          </div>
          <div>
            <dt>Review required</dt>
            <dd>{detail.evaluation.requires_review ? 'Yes' : 'No'}</dd>
          </div>
        </dl>
      </section>

      <div className="criterion-grid">
        <section className="detail-card" aria-labelledby="trial-text-title">
          <p className="eyebrow">Original trial text</p>
          <h3 id="trial-text-title">{detail.criterion.category} criterion</h3>
          <blockquote>{detail.criterion.source_text}</blockquote>
          <p className="code">
            Source span {detail.criterion.source_start}–{detail.criterion.source_end} ·
            parser {detail.criterion.parser_version}
          </p>
          {detail.criterion.requires_human_review && (
            <p className="review-flag">
              The source parser marked this criterion for human review.
            </p>
          )}
        </section>
        <section className="detail-card" aria-labelledby="parsed-criterion-title">
          <p className="eyebrow">Parsed criterion data</p>
          <h3 id="parsed-criterion-title">Deterministic rule</h3>
          <pre>{JSON.stringify(detail.criterion.parsed_data, null, 2)}</pre>
          <dl className="detail-meta">
            <div>
              <dt>Parser confidence</dt>
              <dd>{detail.criterion.parser_confidence ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{displayDate(detail.criterion.created_at)}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="detail-card" aria-labelledby="evaluation-title">
        <p className="eyebrow">Evaluation path</p>
        <h3 id="evaluation-title">
          {detail.evaluation.explanation.replace(/_/g, ' ')}
        </h3>
        <p>
          Version <span className="mono">{detail.evaluation.evaluator_version}</span>{' '}
          used the listed patient-import evidence. Missing, stale, or conflicting
          evidence remains a review signal.
        </p>
      </section>

      <EvidenceList detail={detail} />
      <ReviewCorrectionForm detail={detail} onCorrected={onCorrected} />
      <AuditHistory history={detail.audit_history} />
    </div>
  );
}

function EvidenceList({ detail }: { detail: CriterionDetail }) {
  return (
    <section className="detail-card evidence-list" aria-labelledby="evidence-title">
      <p className="eyebrow">Patient evidence</p>
      <h3 id="evidence-title">{detail.patient_evidence.length} cited fact(s)</h3>
      {detail.patient_evidence.length === 0 ? (
        <p className="unknown-note">
          No source fact was cited. This is consistent with an unknown outcome only.
        </p>
      ) : (
        <div className="evidence-grid">
          {detail.patient_evidence.map((fact) => {
            const stale = fact.quality_issues.some((issue) => issue.code === 'stale');
            return (
              <article
                className={stale ? 'evidence-card stale' : 'evidence-card'}
                key={fact.fact_id}
              >
                <div>
                  <p className="eyebrow">{fact.kind}</p>
                  <h4>{fact.code.display ?? fact.code.value}</h4>
                  <p>{displayFactValue(fact)}</p>
                </div>
                <dl className="detail-meta">
                  <div>
                    <dt>Recorded</dt>
                    <dd>{displayDate(fact.effective_at)}</dd>
                  </div>
                  <div>
                    <dt>Freshness</dt>
                    <dd>{factFreshness(fact)}</dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd>
                      <a
                        href={apiPath(
                          `/patients/${encodeURIComponent(detail.patient_id)}/facts/${encodeURIComponent(fact.fact_id)}/source`,
                        )}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {fact.source.resource_type}/{fact.source.resource_id}
                      </a>
                    </dd>
                  </div>
                </dl>
                {stale && (
                  <p className="stale-note">
                    Stale evidence requires reviewer attention.
                  </p>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ReviewCorrectionForm({
  detail,
  onCorrected,
}: {
  detail: CriterionDetail;
  onCorrected: () => void;
}) {
  const [reviewerId, setReviewerId] = useState('');
  const [correctedOutcome, setCorrectedOutcome] = useState<CriterionOutcome>('unknown');
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(undefined);
    setBusy(true);
    try {
      await requestJson(
        `/criterion-results/${encodeURIComponent(detail.evaluation.id)}/corrections`,
        {
          method: 'POST',
          body: JSON.stringify({
            reviewer_id: reviewerId,
            corrected_outcome: correctedOutcome,
            reason,
          }),
        },
      );
      setReason('');
      onCorrected();
    } catch {
      setMessage(
        'The reviewer correction could not be saved. Select a different outcome and provide a reason.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="detail-card correction-form" aria-labelledby="correction-title">
      <p className="eyebrow">Reviewer correction</p>
      <h3 id="correction-title">Append a correction</h3>
      <p>
        Creates a new audit entry; it never overwrites the deterministic evaluation.
      </p>
      <form onSubmit={submit}>
        <div className="correction-fields">
          <label htmlFor="reviewer-id">
            Reviewer ID
            <input
              id="reviewer-id"
              value={reviewerId}
              onChange={(event) => setReviewerId(event.target.value)}
              required
            />
          </label>
          <label htmlFor="corrected-outcome">
            Corrected outcome
            <select
              id="corrected-outcome"
              value={correctedOutcome}
              onChange={(event) =>
                setCorrectedOutcome(event.target.value as CriterionOutcome)
              }
            >
              {(['met', 'not_met', 'unknown', 'conflicting'] as CriterionOutcome[]).map(
                (outcome) => (
                  <option
                    disabled={outcome === detail.evaluation.current_outcome}
                    key={outcome}
                    value={outcome}
                  >
                    {outcome.replace('_', ' ')}
                  </option>
                ),
              )}
            </select>
          </label>
        </div>
        <label htmlFor="correction-reason">
          Reason for correction
          <textarea
            id="correction-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            minLength={3}
            maxLength={2000}
            required
          />
        </label>
        {message && (
          <p className="error" role="alert">
            {message}
          </p>
        )}
        <button
          className="button"
          disabled={busy || correctedOutcome === detail.evaluation.current_outcome}
          type="submit"
        >
          {busy ? 'Saving…' : 'Append correction'}
        </button>
      </form>
    </section>
  );
}

function AuditHistory({ history }: { history: CriterionDetail['audit_history'] }) {
  return (
    <section className="detail-card audit-history" aria-labelledby="audit-title">
      <p className="eyebrow">Audit history</p>
      <h3 id="audit-title">Evaluation and corrections</h3>
      <ol>
        {history.map((event) => (
          <li key={event.id}>
            <div>
              <strong>{event.event_type.replace('_', ' ')}</strong>
              <p>{event.reason.replace(/_/g, ' ')}</p>
            </div>
            <dl className="detail-meta">
              <div>
                <dt>Actor</dt>
                <dd>{event.actor_id}</dd>
              </div>
              <div>
                <dt>Outcome</dt>
                <dd>
                  {event.previous_outcome
                    ? `${event.previous_outcome} → ${event.outcome}`
                    : event.outcome}
                </dd>
              </div>
              <div>
                <dt>When</dt>
                <dd>{displayDate(event.occurred_at)}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ol>
    </section>
  );
}

function criterionOutcomeTone(
  category: CriterionDetail['criterion']['category'],
  outcome: CriterionOutcome,
): 'unknown' | 'conflicting' | 'excluded' | 'resolved' {
  if (outcome === 'unknown') return 'unknown';
  if (outcome === 'conflicting') return 'conflicting';
  if (category === 'exclusion' && outcome === 'not_met') return 'excluded';
  return 'resolved';
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

export default App;
