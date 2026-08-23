import { describe, expect, it } from 'vitest';

import { apiPath } from '../api';
import {
  buildBoundedTrialSyncRequest,
  displayFactValue,
  factFreshness,
  filterMatchCandidates,
  Fact,
  MatchCandidate,
  retrievalReason,
} from '../clinical';

const observation: Fact = {
  fact_id: 'fact-1',
  kind: 'observation',
  code: { system: 'http://loinc.org', value: '2345-7', display: 'Glucose' },
  value: { numeric_value: 7.2, status: 'final' },
  unit: 'mmol/L',
  effective_at: '2026-08-23T00:00:00Z',
  source: { resource_type: 'Observation', resource_id: 'obs-1' },
  quality_issues: [],
};

describe('clinical display helpers', () => {
  it('keeps advanced catalogue selections bounded before queueing', () => {
    expect(
      buildBoundedTrialSyncRequest({
        nctId: '',
        queryTerm: '',
        condition: 'diabetes',
        startPage: '1',
        endPage: '2',
        pageSize: '25',
      }),
    ).toEqual({
      condition: 'diabetes',
      start_page: 1,
      end_page: 2,
      page_size: 25,
    });
    expect(() =>
      buildBoundedTrialSyncRequest({
        nctId: 'NCT02434107',
        queryTerm: 'melanoma',
        condition: '',
        startPage: '',
        endPage: '',
        pageSize: '25',
      }),
    ).toThrow('Choose one source selection');
  });

  it('keeps fact values and their supplied units visible', () => {
    expect(displayFactValue(observation)).toBe(
      'numeric value: 7.2 · status: final mmol/L',
    );
  });

  it('does not hide a recorded stale-data warning', () => {
    expect(
      factFreshness({
        ...observation,
        quality_issues: [
          { code: 'stale', field: 'effectiveDateTime', message: 'Source is stale.' },
        ],
      }),
    ).toBe('Flagged stale');
  });

  it('keeps source-resource links behind the configured API base', () => {
    expect(apiPath('/patients/a/facts/fact-1/source')).toBe(
      '/api/patients/a/facts/fact-1/source',
    );
  });

  it('filters each outcome tab independently from status and title search', () => {
    const candidates: MatchCandidate[] = [
      {
        id: '1',
        patient_id: 'synthetic-patient-1',
        nct_id: 'NCT00000001',
        title: 'Diabetes study',
        study_status: 'RECRUITING',
        candidate_rank: 1,
        outcome: 'potential_match',
        criterion_results: [],
      },
      {
        id: '2',
        patient_id: 'synthetic-patient-1',
        nct_id: 'NCT00000002',
        title: 'Diabetes extension',
        study_status: 'COMPLETED',
        candidate_rank: 2,
        outcome: 'needs_review',
        criterion_results: [],
      },
      {
        id: '3',
        patient_id: 'synthetic-patient-1',
        nct_id: 'NCT00000003',
        title: 'Heart study',
        study_status: 'RECRUITING',
        candidate_rank: 3,
        outcome: 'likely_excluded',
        criterion_results: [],
      },
    ];

    expect(
      filterMatchCandidates(candidates, 'needs_review', 'diabetes', 'COMPLETED'),
    ).toEqual([candidates[1]]);
    expect(
      filterMatchCandidates(candidates, 'likely_exclusions', '', 'RECRUITING'),
    ).toEqual([candidates[2]]);
  });

  it('explains a listed trial with deterministic retrieval fields, not an outcome', () => {
    expect(
      retrievalReason({
        score: 4,
        matched_term_count: 1,
        query_term_count: 2,
        matched_fields: ['conditions', 'title'],
        matched_fact_ids: ['fact-1'],
      }),
    ).toBe('Documented patient-fact terms matched the trial conditions, title fields.');
  });
});
