export type Fact = {
  fact_id: string;
  kind: string;
  code: { system: string; value: string; display?: string | null };
  value: unknown;
  unit?: string | null;
  effective_at?: string | null;
  source: { resource_type: string; resource_id: string; version_id?: string | null };
  quality_issues: QualityIssue[];
};

export type QualityIssue = {
  code: 'missing' | 'stale' | 'invalid' | 'conflicting';
  field: string;
  message: string;
};

export type Timeline = {
  patient_id: string;
  synthetic: boolean;
  import_snapshot: {
    id: string;
    fhir_version: string;
    source_hash: string;
    created_at: string;
    completed_at?: string | null;
    data_quality_issues: QualityIssue[];
  } | null;
  facts: Fact[];
};

export type ImportResult = {
  patient_id: string;
  patient_import_id: string;
  fact_ids: string[];
  data_quality_issues: QualityIssue[];
};

export type MatchRun = {
  id: string;
  patient_import_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  candidate_count: number;
  candidate_limit: number;
  cancellation_requested: boolean;
  configuration_versions: Record<string, string>;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  failure?: { code: string; message: string } | null;
};

export type MatchCandidate = {
  id: string;
  patient_id: string;
  nct_id: string;
  title?: string | null;
  study_status?: string | null;
  source_updated_at?: string | null;
  candidate_rank: number;
  retrieval_relevance?: {
    score: number;
    matched_term_count: number;
    query_term_count: number;
    matched_fields: ('conditions' | 'title' | 'interventions' | 'eligibility_text')[];
    matched_fact_ids: string[];
  } | null;
  criterion_results: {
    id: string;
    category: 'inclusion' | 'exclusion';
    source_text: string;
    outcome: CriterionOutcome;
    current_outcome: CriterionOutcome;
    requires_review: boolean;
  }[];
  outcome?:
    'potential_match' | 'likely_excluded' | 'needs_review' | 'not_relevant' | null;
};

export type CriterionOutcome = 'met' | 'not_met' | 'unknown' | 'conflicting';

export type CriterionDetail = {
  patient_id: string;
  trial_match_id: string;
  criterion: {
    id: string;
    category: 'inclusion' | 'exclusion';
    source_text: string;
    source_start: number;
    source_end: number;
    parsed_data: Record<string, unknown>;
    parser_version: string;
    parser_confidence?: string | number | null;
    requires_human_review: boolean;
    created_at: string;
  };
  evaluation: {
    id: string;
    outcome: CriterionOutcome;
    current_outcome: CriterionOutcome;
    evidence_fact_ids: string[];
    evaluator_version: string;
    evaluation_path: string;
    explanation: string;
    requires_review: boolean;
    evaluated_at: string;
  };
  patient_evidence: Fact[];
  audit_history: {
    id: string;
    event_type: 'deterministic_evaluation' | 'review_correction';
    occurred_at: string;
    actor_id: string;
    outcome: CriterionOutcome;
    previous_outcome?: CriterionOutcome | null;
    reason: string;
    evaluation_path?: string | null;
  }[];
};

export function displayDate(value?: string | null): string {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(value))
    : 'Not recorded';
}

export function displayFactValue(fact: Fact): string {
  if (fact.value === null || fact.value === undefined) return 'Not recorded';
  const value =
    typeof fact.value === 'object'
      ? Object.entries(fact.value as Record<string, unknown>)
          .filter(([, item]) => item !== null && item !== undefined && item !== '')
          .map(([key, item]) => `${key.replace(/_/g, ' ')}: ${String(item)}`)
          .join(' · ')
      : String(fact.value);
  return fact.unit ? `${value} ${fact.unit}` : value;
}

export function factFreshness(fact: Fact): string {
  if (fact.quality_issues.some((issue) => issue.code === 'stale'))
    return 'Flagged stale';
  if (!fact.effective_at) return 'No recorded effective date';
  const age = Math.max(
    0,
    Math.floor((Date.now() - new Date(fact.effective_at).getTime()) / 86_400_000),
  );
  return age === 0 ? 'Recorded today' : `${age} days since recorded`;
}

export function retrievalReason(
  relevance: MatchCandidate['retrieval_relevance'],
): string {
  if (!relevance) return 'Retrieval relevance details are not available.';
  if (relevance.matched_fields.length === 0)
    return 'Retrieved by a documented lexical match; the matching trial field was not retained.';
  const fields = relevance.matched_fields
    .map((field) => field.replace(/_/g, ' '))
    .join(', ');
  return `Documented patient-fact terms matched the trial ${fields} field${relevance.matched_fields.length === 1 ? '' : 's'}.`;
}

export type ResultTab =
  'all' | 'potential_matches' | 'needs_review' | 'likely_exclusions';

export function filterMatchCandidates(
  candidates: MatchCandidate[],
  tab: ResultTab,
  search: string,
  studyStatus: string,
): MatchCandidate[] {
  const normalizedSearch = search.trim().toLocaleLowerCase();
  return candidates.filter((candidate) => {
    const candidateStatus = candidate.study_status ?? '';
    const matchesStatus =
      studyStatus === 'all' ||
      (studyStatus === 'not_recorded'
        ? !candidateStatus
        : candidateStatus === studyStatus);
    const searchableText =
      `${candidate.nct_id} ${candidate.title ?? ''} ${candidateStatus}`.toLocaleLowerCase();
    return (
      matchesStatus &&
      matchesResultTab(candidate, tab) &&
      (!normalizedSearch || searchableText.includes(normalizedSearch))
    );
  });
}

function matchesResultTab(candidate: MatchCandidate, tab: ResultTab): boolean {
  if (tab === 'all') return true;
  if (tab === 'potential_matches') return candidate.outcome === 'potential_match';
  if (tab === 'needs_review')
    return (
      candidate.outcome === 'needs_review' ||
      candidate.outcome === null ||
      candidate.outcome === undefined
    );
  return candidate.outcome === 'likely_excluded';
}
