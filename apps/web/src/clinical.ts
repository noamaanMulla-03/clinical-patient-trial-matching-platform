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

export type TrialSyncSelection = {
  mode: 'nct_id' | 'search' | 'page_range';
  collection_id?: string | null;
  nct_id?: string | null;
  query_term?: string | null;
  condition?: string | null;
  start_page?: number | null;
  end_page?: number | null;
  page_size: number;
};

export type TrialSync = {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  selection: TrialSyncSelection;
  counts: {
    pages_fetched: number;
    studies_processed: number;
    versions_created: number;
    unchanged_studies: number;
    versions_requiring_reparse: number;
    versions_reusing_matching_results: number;
  };
  source_lag: {
    records_with_update_time: number;
    records_missing_update_time: number;
    records_invalid_update_time: number;
    max_lag_seconds?: number | null;
  };
  failure?: { code: string; message: string } | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
};

export type TrialCatalogueStatus = {
  state: 'empty' | 'ready' | 'updating';
  searchable_trial_count: number;
  latest_successful_update_at?: string | null;
  latest_sync?: TrialSync | null;
  freshness: {
    records_with_source_update_time: number;
    records_missing_source_update_time: number;
    oldest_source_update_at?: string | null;
    newest_source_update_at?: string | null;
    latest_retrieved_at?: string | null;
  };
};

export type TrialCatalogueTrial = {
  nct_id: string;
  title?: string | null;
  study_status?: string | null;
  source_updated_at?: string | null;
  retrieved_at: string;
};

export type TrialCatalogueTrials = {
  total_count: number;
  items: TrialCatalogueTrial[];
};

export type TrialSyncCreateInput = {
  nctId: string;
  queryTerm: string;
  condition: string;
  startPage: string;
  endPage: string;
  pageSize: string;
};

export type BoundedTrialSyncRequest = {
  nct_id?: string;
  query_term?: string;
  condition?: string;
  start_page?: number;
  end_page?: number;
  page_size: number;
};

export function buildBoundedTrialSyncRequest(
  input: TrialSyncCreateInput,
): BoundedTrialSyncRequest {
  const nctId = input.nctId.trim();
  const queryTerm = input.queryTerm.trim();
  const condition = input.condition.trim();
  const selectors = [nctId, queryTerm, condition].filter(Boolean);
  const hasStartPage = input.startPage.trim() !== '';
  const hasEndPage = input.endPage.trim() !== '';
  const pageSize = Number(input.pageSize);

  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 1_000)
    throw new Error('Page size must be a whole number between 1 and 1000.');
  if (selectors.length > 1)
    throw new Error(
      'Choose one source selection: NCT ID, condition, or search phrase.',
    );
  if (hasStartPage !== hasEndPage)
    throw new Error('Enter both page-range values or leave both blank.');

  const request: BoundedTrialSyncRequest = { page_size: pageSize };
  if (nctId) request.nct_id = nctId;
  if (queryTerm) request.query_term = queryTerm;
  if (condition) request.condition = condition;

  if (hasStartPage && hasEndPage) {
    const startPage = Number(input.startPage);
    const endPage = Number(input.endPage);
    if (
      !Number.isInteger(startPage) ||
      !Number.isInteger(endPage) ||
      startPage < 1 ||
      endPage < startPage
    )
      throw new Error('Page range must use whole pages, starting at 1.');
    if (nctId) throw new Error('A specific NCT ID cannot include a page range.');
    request.start_page = startPage;
    request.end_page = endPage;
  } else if (selectors.length === 0) {
    throw new Error('Choose a source selection or enter an explicit page range.');
  }

  return request;
}

export type MatchCandidate = {
  id: string;
  patient_id: string;
  nct_id: string;
  title?: string | null;
  study_status?: string | null;
  source_updated_at?: string | null;
  candidate_rank: number;
  retrieval_sources: ('lexical' | 'semantic')[];
  retrieval_relevance?: {
    score: number;
    matched_term_count: number;
    query_term_count: number;
    matched_fields: ('conditions' | 'title' | 'interventions' | 'eligibility_text')[];
    matched_fact_ids: string[];
  } | null;
  semantic_relevance?: {
    score: number;
    rank: number;
  } | null;
  fused_relevance?: {
    method: 'reciprocal-rank-fusion-v1';
    score: number;
    rank: number;
    rank_constant: number;
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

export function semanticRetrievalReason(
  relevance: MatchCandidate['semantic_relevance'],
): string {
  if (!relevance) return 'Semantic retrieval details are not available.';
  return 'Listed from similarity between the synthetic patient context and public trial text; review the source trial criteria before relying on it.';
}

export function fusedRetrievalReason(
  relevance: MatchCandidate['fused_relevance'],
  sources: MatchCandidate['retrieval_sources'],
): string {
  if (!relevance) return 'Combined retrieval details are not available.';
  if (sources.includes('lexical') && sources.includes('semantic'))
    return 'Lexical matching and semantic similarity both contributed to this combined retrieval rank; review the trial criteria and source facts.';
  if (sources.includes('lexical'))
    return 'This combined retrieval rank was based on documented lexical matching; review the linked source facts.';
  return 'This combined retrieval rank was based on semantic similarity to public trial text; review the trial criteria before relying on it.';
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
