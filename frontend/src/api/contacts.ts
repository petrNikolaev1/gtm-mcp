// Types only — api calls are in index.ts

export interface Contact {
  id: number;
  email: string;
  first_name?: string;
  last_name?: string;
  company_name?: string;
  domain?: string;
  job_title?: string;
  segment?: string;
  suitable_for?: string[];
  geo?: string;
  project_id?: number;
  project_name?: string;
  source: string;
  source_id?: string;
  status: string;
  status_external?: string;
  phone?: string;
  linkedin_url?: string;
  location?: string;
  notes?: string;
  smartlead_id?: string;
  getsales_id?: string;
  // Canonical funnel
  last_reply_at?: string;
  has_replied?: boolean;       // computed by backend from last_reply_at
  needs_followup?: boolean;
  // Reply classification (enriched from ProcessedReply)
  latest_reply_category?: string;
  latest_reply_confidence?: string;
  // Canonical data
  provenance?: Record<string, any>;
  platform_state?: Record<string, any>;
  campaigns?: Array<{ id: string; name: string; source: string; status?: string }>;
  // Timestamps
  created_at: string;
  updated_at: string;
}

export interface ContactListResponse {
  contacts: Contact[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ContactStats {
  total: number;
  by_status: Record<string, number>;
  by_segment: Record<string, number>;
  by_source: Record<string, number>;
  by_project: Record<string, number>;
}

export interface FilterOptions {
  statuses: string[];
  sources: string[];
  segments: string[];
  geos: string[];
  projects: Array<{ id: number; name: string }>;
}

export interface SheetSyncConfig {
  enabled: boolean;
  sheet_id: string;
  leads_tab?: string;
  replies_tab?: string;
  last_replies_sync_at?: string;
  last_leads_push_at?: string;
  last_qualification_poll_at?: string;
  replies_synced_count?: number;
  leads_pushed_count?: number;
  last_error?: string | null;
  last_error_at?: string | null;
}

export interface CampaignOwnershipRules {
  prefixes?: string[];
  contains?: string[];
  smartlead_tags?: string[];
}

export interface Project {
  id: number;
  name: string;
  description?: string;
  target_industries?: string;
  target_segments?: string;
  campaign_filters?: string[];
  campaign_ownership_rules?: CampaignOwnershipRules | null;
  telegram_chat_id?: string;
  telegram_username?: string;
  webhooks_enabled?: boolean;
  sheet_sync_config?: SheetSyncConfig | null;
  sdr_email?: string;  // SDR email for test campaign notifications
  contact_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectLite {
  id: number;
  name: string;
  campaign_filters: string[];
  telegram_username?: string;
}

export interface AISDRProject extends Project {
  tam_analysis?: string;
  gtm_plan?: string;
  pitch_templates?: string;
}

export interface AISDRGenerationResult {
  success: boolean;
  tam_analysis?: string;
  gtm_plan?: string;
  pitch_templates?: string;
}

export interface TelegramSubscriber {
  id: number;
  chat_id: string;
  username: string | null;
  first_name: string | null;
  subscribed_at: string | null;
}

export interface TelegramStatus {
  connected: boolean;
  first_name?: string;
  username?: string;
  subscribers: TelegramSubscriber[];
}

export interface ProjectMonitoring {
  project: { id: number; name: string; webhooks_enabled: boolean };
  scheduler: { running: boolean; task_health: Record<string, string> };
  webhooks: { healthy: boolean; last_received: string | null; last_check: string | null };
  polling: {
    intervals: { task: string; interval_seconds: number | null; last_run: string | null; next_run: string | null }[];
    reply_checks_count: number;
    sync_count: number;
  };
  reply_stats: {
    total_contacts: number;
    total_replied: number;
    replies_24h: number;
    replies_7d: number;
    failed_events_24h: number;
  };
  campaigns: {
    name: string;
    platform: string;
    status: string;
    active: boolean;
    contacts: number;
    replied: number;
    external_id: string | null;
  }[];
  active_campaigns_count: number;
  latest_events: {
    events: {
      id: string;
      type: string;
      source: string | null;
      channel: string | null;
      campaign_name: string | null;
      lead_email: string | null;
      lead_name: string | null;
      category: string | null;
      approval_status: string | null;
      at: string | null;
      error?: string | null;
    }[];
  };
}

export interface GTMData {
  project_id: number;
  project_name: string;
  total_contacts: number;
  classified: number;
  unclassified: number;
  avg_confidence: number | null;
  segments: Array<{ segment: string; count: number }>;
  industries: Array<{ industry: string; count: number }>;
  top_keywords: Array<{ keyword: string; count: number }>;
  cross_project_matches: Array<{ target: string; count: number }>;
  status_by_segment: Record<string, Record<string, number>>;
  gtm_plan: string | null;
}

export interface SegmentFunnelSegment {
  segment: string;
  total_contacts: number;
  total_replies: number;
  positive: number;
  meeting_requests: number;
  interested: number;
  questions: number;
  not_interested: number;
  ooo: number;
  reply_rate: number;
  positive_rate: number;
}

export interface SegmentFunnelData {
  project_id: number;
  period: string;
  totals: {
    total_contacts: number;
    total_replies: number;
    positive: number;
    meeting_requests: number;
  };
  segments: SegmentFunnelSegment[];
}

export interface GTMStrategyLogEntry {
  id: number;
  trigger: string;
  model: string;
  status: string;
  input_summary: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: string | null;
  error_message: string | null;
  created_at: string | null;
  has_strategy: boolean;
}

export interface GTMStrategyLogsResponse {
  items: GTMStrategyLogEntry[];
  total: number;
}

export interface ImportResult {
  success: boolean;
  total_rows: number;
  created: number;
  skipped: number;
  errors: string[];
  sample_created: string[];
}

export interface EnrichResult {
  success: boolean;
  total_rows: number;
  enriched: number;
  skipped: number;
  not_found: number;
  errors: string[];
}

export interface ContactCreate {
  email: string;
  first_name?: string;
  last_name?: string;
  company_name?: string;
  domain?: string;
  job_title?: string;
  segment?: string;
  project_id?: number;
  source?: string;
  status?: string;
  phone?: string;
  linkedin_url?: string;
  location?: string;
  notes?: string;
}

// Activity tracking
export interface ContactActivity {
  id: number;
  contact_id: number;
  activity_type: string;
  channel: string;
  direction?: string;
  source: string;
  subject?: string;
  snippet?: string;
  activity_at: string;
  created_at: string;
}

export interface ContactWithActivities extends Contact {
  activities: ContactActivity[];
}

export interface SyncStatus {
  total_contacts: number;
  by_source: Record<string, number>;
  replied_contacts: number;
  total_activities: number;
}

export interface ContactFilters {
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  search?: string;
  project_id?: number;
  segment?: string;
  geo?: string;
  status?: string;
  source?: string;
  has_replied?: boolean;
  has_smartlead?: boolean;
  has_getsales?: boolean;
  campaign?: string;
  campaign_id?: string;
  needs_followup?: boolean;
  smartlead_id?: string;
  getsales_id?: string;
  created_after?: string;
  created_before?: string;
  domain?: string;
  suitable_for?: string;
  reply_category?: string;
  reply_since?: string;
  source_id?: string;
  is_qualified?: boolean;
}

export interface GenerateReplyResponse {
  has_reply: boolean;
  cached?: boolean;
  category?: string;
  draft_subject?: string;
  draft_body?: string;
  channel?: string;
  reply_text?: string;
  message?: string;
  error?: string;
  contact?: { name: string; email: string; company: string };
}

export interface OperatorTask {
  id: number;
  project_id?: number;
  contact_id?: number;
  task_type: string;
  title: string;
  description?: string;
  due_at: string;
  status: string;
  contact_email?: string;
  contact_name?: string;
  created_at: string;
  updated_at: string;
}

export interface TasksListResponse {
  tasks: OperatorTask[];
  total: number;
  pending: number;
  done: number;
}
