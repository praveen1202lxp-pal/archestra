export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
  children?: FileItem[];
  expanded?: boolean;
}

export interface EditorTab {
  id: string;
  path: string;
  name: string;
  content: string;
  originalContent: string;
  isDirty: boolean;
  language: string;
}

export interface TaskSummary {
  id: string;
  title: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  recoverable?: boolean;
  last_checkpoint_sha?: string | null;
  promotion_disposition?: string | null;
}

export interface TaskTimelineStep {
  id: string;
  label: string;
  status: 'pending' | 'active' | 'completed' | 'failed';
  detail?: string;
  timestamp?: string;
}

export interface VerificationResultData {
  passed: boolean;
  exit_code: number;
  duration_seconds: number;
  stdout?: string;
  stderr?: string;
}

export interface ReviewFindingData {
  status: string;
  reviewer_agent: string;
  comments: string;
}

export interface ActiveTaskState {
  task_id?: string;
  instruction: string;
  status: string;
  timeline: TaskTimelineStep[];
  diff?: string;
  verification?: VerificationResultData | null;
  review?: ReviewFindingData | null;
  final_answer?: string;
  is_running: boolean;
  eligible_for_approval?: boolean;
  logs: string[];
}

export interface ProviderHealth {
  id: string;
  name: string;
  type: string;
  model?: string;
  healthy: boolean;
  message: string;
  latency_ms: number;
}

export interface ProjectInfo {
  opened: boolean;
  path: string;
  name: string;
  initialized: boolean;
  version: string;
  optimization_mode: string;
  verification_command: string;
  git_clean: boolean;
  config?: any;
}

export interface DiagnosticCheckItem {
  name: string;
  status: 'PASS' | 'WARN' | 'FAIL';
  message: string;
  remediation?: string | null;
}

export interface DoctorReportData {
  is_healthy: boolean;
  passed_count: number;
  warn_count: number;
  fail_count: number;
  checks: DiagnosticCheckItem[];
}
