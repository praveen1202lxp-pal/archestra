import { Injectable } from '@angular/core';
import { Observable, Subject, of } from 'rxjs';
import {
  DoctorReportData,
  FileItem,
  ProjectInfo,
  ProviderHealth,
  TaskSummary,
} from '../models/studio.models';

declare global {
  interface Window {
    __TAURI__?: {
      invoke: (cmd: string, args?: any) => Promise<any>;
    };
  }
}

@Injectable({
  providedIn: 'root',
})
export class BridgeService {
  private eventsSubject = new Subject<{ event: string; data: any }>();
  public events$: Observable<{ event: string; data: any }> = this.eventsSubject.asObservable();

  constructor() {}

  public isTauri(): boolean {
    return typeof window !== 'undefined' && !!window.__TAURI__;
  }

  public async sendCommand(command: string, params: any = {}): Promise<any> {
    const request = {
      id: 'req-' + Math.random().toString(36).substring(2, 9),
      command,
      params,
    };

    if (this.isTauri()) {
      try {
        const resp = await window.__TAURI__!.invoke('send_bridge_command', { request });
        if (resp && resp.event) {
          this.eventsSubject.next(resp);
        }
        return resp;
      } catch (err) {
        return { success: false, error: { message: String(err) } };
      }
    }

    // Browser development fallback mode
    return this.mockDevFallback(command, params);
  }

  // High-level API methods
  public async openProject(path: string): Promise<any> {
    return this.sendCommand('OPEN_PROJECT', { path });
  }

  public async initProject(name?: string): Promise<any> {
    return this.sendCommand('INIT_PROJECT', { name });
  }

  public async getProjectStatus(): Promise<any> {
    return this.sendCommand('GET_PROJECT_STATUS');
  }

  public async getProviderStatus(): Promise<any> {
    return this.sendCommand('GET_PROVIDER_STATUS');
  }

  public async getMcpStatus(): Promise<any> {
    return this.sendCommand('GET_MCP_STATUS');
  }

  public async listFiles(subpath: string = ''): Promise<any> {
    return this.sendCommand('LIST_FILES', { subpath });
  }

  public async readFile(filepath: string): Promise<any> {
    return this.sendCommand('READ_FILE', { filepath });
  }

  public async writeFile(filepath: string, content: string): Promise<any> {
    return this.sendCommand('WRITE_FILE', { filepath, content });
  }

  public async listTasks(limit: number = 20): Promise<any> {
    return this.sendCommand('LIST_TASKS', { limit });
  }

  public async getTask(taskId: string): Promise<any> {
    return this.sendCommand('GET_TASK', { task_id: taskId });
  }

  public async startTask(instruction: string): Promise<any> {
    return this.sendCommand('START_TASK', { instruction });
  }

  public async resumeTask(taskId: string): Promise<any> {
    return this.sendCommand('RESUME_TASK', { task_id: taskId });
  }

  public async getDiff(taskId?: string): Promise<any> {
    return this.sendCommand('GET_DIFF', { task_id: taskId });
  }

  public async approveTask(taskId: string): Promise<any> {
    return this.sendCommand('APPROVE_TASK', { task_id: taskId });
  }

  public async rejectTask(taskId: string): Promise<any> {
    return this.sendCommand('REJECT_TASK', { task_id: taskId });
  }

  public async getTestResults(taskId?: string): Promise<any> {
    return this.sendCommand('GET_TEST_RESULTS', { task_id: taskId });
  }

  public async getReviewFindings(taskId?: string): Promise<any> {
    return this.sendCommand('GET_REVIEW_FINDINGS', { task_id: taskId });
  }

  public async runDoctor(): Promise<any> {
    return this.sendCommand('RUN_DOCTOR');
  }

  public async saveConfig(config: any): Promise<any> {
    return this.sendCommand('SAVE_CONFIG', { config });
  }

  // --- Browser Mock Dev Fallback ---
  private mockDevFallback(command: string, params: any): Promise<any> {
    switch (command) {
      case 'OPEN_PROJECT':
      case 'GET_PROJECT_STATUS':
        return Promise.resolve({
          success: true,
          data: {
            opened: true,
            project_name: 'archestra',
            project_root: params.path || 'c:/Users/pallu/Desktop/archestra',
            initialized: true,
            version: '0.12.0',
            optimization_mode: 'BALANCED',
            verification_command: 'pytest',
            git_clean: true,
          },
        });
      case 'GET_PROVIDER_STATUS':
        return Promise.resolve({
          success: true,
          data: {
            providers: [
              {
                id: 'codex',
                name: 'OpenAI Codex CLI',
                type: 'codex_cli',
                model: 'gpt-5.6-sol',
                healthy: true,
                message: 'CLI ready on PATH',
                latency_ms: 18.4,
              },
              {
                id: 'antigravity',
                name: 'Google Antigravity CLI',
                type: 'antigravity_cli',
                model: 'gemini-3.8-flash-high',
                healthy: true,
                message: 'CLI ready on PATH',
                latency_ms: 24.1,
              },
            ],
          },
        });
      case 'LIST_FILES':
        return Promise.resolve({
          success: true,
          data: {
            subpath: params.subpath || '',
            files: [
              { name: 'src', path: 'src', is_dir: true },
              { name: 'tests', path: 'tests', is_dir: true },
              { name: 'fusion_agent', path: 'fusion_agent', is_dir: true },
              { name: 'README.md', path: 'README.md', is_dir: false, size: 18658 },
              { name: 'pyproject.toml', path: 'pyproject.toml', is_dir: false, size: 1095 },
            ],
          },
        });
      case 'READ_FILE':
        return Promise.resolve({
          success: true,
          data: {
            filepath: params.filepath,
            content: `# File: ${params.filepath}\n\ndef example():\n    return "Loaded via Fusion UI Bridge"\n`,
          },
        });
      case 'WRITE_FILE':
        return Promise.resolve({
          success: true,
          data: { filepath: params.filepath, bytes_written: (params.content || '').length },
        });
      case 'LIST_TASKS':
        return Promise.resolve({
          success: true,
          data: {
            tasks: [
              {
                id: 'task-42',
                title: 'Add validation to signup endpoint',
                status: 'COMPLETED',
                created_at: new Date(Date.now() - 3600000).toISOString(),
                completed_at: new Date(Date.now() - 3400000).toISOString(),
                promotion_disposition: 'PROMOTED',
              },
              {
                id: 'task-43',
                title: 'Refactor cache key hashing',
                status: 'FAILED',
                created_at: new Date(Date.now() - 7200000).toISOString(),
                promotion_disposition: 'BLOCKED',
              },
              {
                id: 'task-44',
                title: 'Add timeout parameter to HTTP client',
                status: 'RECOVERABLE',
                created_at: new Date(Date.now() - 10800000).toISOString(),
                recoverable: true,
                last_checkpoint_sha: 'a1b2c3d4',
              },
            ],
          },
        });
      case 'START_TASK':
        // Emit simulated progression events
        setTimeout(() => this.eventsSubject.next({ event: 'TASK_ASSESSED', data: { strategy: 'AUTONOMOUS_EDIT', primary: 'codex', secondary: 'antigravity' } }), 400);
        setTimeout(() => this.eventsSubject.next({ event: 'CONTEXT_BUILT', data: { message: 'Constructed bounded CodeContext (3 files, ~480 tokens)' } }), 800);
        setTimeout(() => this.eventsSubject.next({ event: 'VERIFICATION_STARTED', data: { message: 'Executing pytest suite...' } }), 1400);
        setTimeout(() => this.eventsSubject.next({ event: 'VERIFICATION_COMPLETED', data: { message: 'Verification passed (14 passed in 0.42s)' } }), 2000);
        setTimeout(() => this.eventsSubject.next({ event: 'REVIEW_STARTED', data: { message: 'Cross-model peer review by Antigravity...' } }), 2400);
        setTimeout(() => this.eventsSubject.next({ event: 'APPROVAL_REQUIRED', data: { task_id: 'task-demo', eligible: true } }), 2800);

        return Promise.resolve({
          success: true,
          data: {
            task_id: 'task-demo',
            status: 'COMPLETED',
            diff: '--- a/src/app.py\n+++ b/src/app.py\n@@ -1,3 +1,5 @@\n def hello():\n-    return "world"\n+    return "world with validation"\n',
            verification_passed: true,
            review_approved: true,
            final_answer: 'Added validation logic to signup endpoint and verified with passing test suite.',
          },
        });
      case 'APPROVE_TASK':
        return Promise.resolve({
          success: true,
          data: { message: 'Changes promoted to master branch cleanly.', promoted: true },
        });
      case 'REJECT_TASK':
        return Promise.resolve({
          success: true,
          data: { message: 'Changes rejected and worktree discarded.', discarded: true },
        });
      case 'RUN_DOCTOR':
        return Promise.resolve({
          success: true,
          data: {
            is_healthy: true,
            passed_count: 5,
            warn_count: 0,
            fail_count: 0,
            checks: [
              { name: 'Python Runtime', status: 'PASS', message: 'Python 3.12 detected.' },
              { name: 'Git Environment', status: 'PASS', message: 'Git 2.40 detected and configured.' },
              { name: 'Storage & SQLite', status: 'PASS', message: 'SQLite WAL mode writable.' },
              { name: 'Provider: Codex', status: 'PASS', message: 'OpenAI Codex CLI detected on PATH.' },
              { name: 'Provider: Antigravity', status: 'PASS', message: 'Google Antigravity CLI detected on PATH.' },
            ],
          },
        });
      default:
        return Promise.resolve({ success: true, data: {} });
    }
  }
}
