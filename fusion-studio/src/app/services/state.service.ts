import { Injectable, signal } from '@angular/core';
import {
  ActiveTaskState,
  DoctorReportData,
  EditorTab,
  FileItem,
  ProjectInfo,
  ProviderHealth,
  TaskSummary,
  TaskTimelineStep,
} from '../models/studio.models';
import { BridgeService } from './bridge.service';

@Injectable({
  providedIn: 'root',
})
export class StateService {
  // Reactive signals
  public project = signal<ProjectInfo>({
    opened: false,
    path: '',
    name: 'Fusion Studio',
    initialized: false,
    version: '0.12.0',
    optimization_mode: 'BALANCED',
    verification_command: 'pytest',
    git_clean: true,
  });

  public files = signal<FileItem[]>([]);
  public openTabs = signal<EditorTab[]>([]);
  public activeTab = signal<EditorTab | null>(null);

  public activeTask = signal<ActiveTaskState>({
    instruction: '',
    status: 'IDLE',
    timeline: [],
    is_running: false,
    logs: [],
  });

  public taskHistory = signal<TaskSummary[]>([]);
  public providers = signal<ProviderHealth[]>([]);
  public doctorReport = signal<DoctorReportData | null>(null);

  public activeMainView = signal<'editor' | 'diff' | 'settings'>('editor');
  public diagnosticsDrawerOpen = signal<boolean>(false);
  public dirtyWarning = signal<string | null>(null);

  constructor(private bridge: BridgeService) {
    this.initEventListeners();
  }

  private initEventListeners() {
    this.bridge.events$.subscribe((msg) => {
      this.handleBridgeEvent(msg.event, msg.data);
    });
  }

  private handleBridgeEvent(event: string, data: any) {
    const current = this.activeTask();
    const logs = [...current.logs];
    const timeline = [...current.timeline];

    switch (event) {
      case 'TASK_STARTED':
        this.activeTask.set({
          ...current,
          is_running: true,
          status: 'RUNNING',
          timeline: [
            { id: '1', label: 'Task initialized', status: 'completed' },
            { id: '2', label: 'Assessing task complexity', status: 'active' },
          ],
          logs: [...logs, `Task started: ${data.instruction || ''}`],
        });
        break;

      case 'TASK_ASSESSED':
        this.updateTimelineStep('2', 'completed', `Strategy: ${data.strategy || 'AUTONOMOUS_EDIT'}`);
        timeline.push({ id: '3', label: 'Building bounded context', status: 'active' });
        this.activeTask.update((s) => ({
          ...s,
          timeline,
          logs: [...s.logs, `Assessed strategy: ${data.strategy} (Implementer: ${data.primary || 'codex'})`],
        }));
        break;

      case 'CONTEXT_BUILT':
        this.updateTimelineStep('3', 'completed', data.message || 'Bounded context extracted');
        timeline.push({ id: '4', label: 'Code synthesis & patching', status: 'active' });
        this.activeTask.update((s) => ({ ...s, timeline }));
        break;

      case 'VERIFICATION_STARTED':
        this.updateTimelineStep('4', 'completed');
        timeline.push({ id: '5', label: 'Automated verification (tests)', status: 'active' });
        this.activeTask.update((s) => ({ ...s, timeline }));
        break;

      case 'VERIFICATION_COMPLETED':
        this.updateTimelineStep('5', 'completed', data.message);
        timeline.push({ id: '6', label: 'Cross-model peer review / critique', status: 'active' });
        this.activeTask.update((s) => ({ ...s, timeline }));
        break;

      case 'REVIEW_STARTED':
        this.updateTimelineStep('6', 'active', data.message);
        break;

      case 'APPROVAL_REQUIRED':
        this.updateTimelineStep('6', 'completed', 'Peer review approved');
        timeline.push({ id: '7', label: 'Human promotion gate', status: 'active', detail: 'Awaiting user confirmation' });
        this.activeTask.update((s) => ({
          ...s,
          timeline,
          eligible_for_approval: true,
          verification: data.verification,
          review: data.review,
          status: 'AWAITING_APPROVAL',
        }));
        this.activeMainView.set('diff');
        break;

      case 'DIFF_READY':
        this.activeTask.update((s) => ({ ...s, diff: data.diff }));
        break;

      case 'TASK_COMPLETED':
        this.activeTask.update((s) => ({
          ...s,
          is_running: false,
          status: 'COMPLETED',
          diff: data.diff || s.diff,
          final_answer: data.final_answer,
        }));
        this.refreshTasks();
        break;

      case 'TASK_FAILED':
        this.activeTask.update((s) => ({
          ...s,
          is_running: false,
          status: 'FAILED',
          logs: [...s.logs, `Error: ${data.error}`],
        }));
        this.refreshTasks();
        break;

      case 'LOG_MESSAGE':
        this.activeTask.update((s) => ({
          ...s,
          logs: [...s.logs, data.message],
        }));
        break;
    }
  }

  private updateTimelineStep(id: string, status: 'pending' | 'active' | 'completed' | 'failed', detail?: string) {
    this.activeTask.update((s) => {
      const steps = s.timeline.map((step) => {
        if (step.id === id) {
          return { ...step, status, detail: detail || step.detail };
        }
        return step;
      });
      return { ...s, timeline: steps };
    });
  }

  // --- Project & Files ---
  public async loadProject(path: string) {
    const res = await this.bridge.openProject(path);
    if (res.success) {
      this.project.set({
        opened: true,
        path: res.data.path,
        name: res.data.name,
        initialized: res.data.initialized,
        version: res.data.version,
        optimization_mode: 'BALANCED',
        verification_command: 'pytest',
        git_clean: res.data.git_clean,
      });
      await this.refreshFiles();
      await this.refreshProviders();
      await this.refreshTasks();
    }
  }

  public async refreshFiles(subpath: string = '') {
    const res = await this.bridge.listFiles(subpath);
    if (res.success) {
      this.files.set(res.data.files);
    }
  }

  public async refreshProviders() {
    const res = await this.bridge.getProviderStatus();
    if (res.success && res.data.providers) {
      this.providers.set(res.data.providers);
    }
  }

  public async refreshTasks() {
    const res = await this.bridge.listTasks();
    if (res.success && res.data.tasks) {
      this.taskHistory.set(res.data.tasks);
    }
  }

  // --- Editor Tabs ---
  public async openFile(path: string) {
    // Check if already open
    const existing = this.openTabs().find((t) => t.path === path);
    if (existing) {
      this.activeTab.set(existing);
      this.activeMainView.set('editor');
      return;
    }

    const res = await this.bridge.readFile(path);
    if (res.success) {
      const name = path.split('/').pop() || path;
      const lang = this.detectLanguage(name);
      const newTab: EditorTab = {
        id: 'tab-' + Math.random().toString(36).substring(2, 7),
        path,
        name,
        content: res.data.content,
        originalContent: res.data.content,
        isDirty: false,
        language: lang,
      };

      this.openTabs.update((tabs) => [...tabs, newTab]);
      this.activeTab.set(newTab);
      this.activeMainView.set('editor');
    }
  }

  public closeTab(tabId: string) {
    const tabs = this.openTabs().filter((t) => t.id !== tabId);
    this.openTabs.set(tabs);
    if (this.activeTab()?.id === tabId) {
      this.activeTab.set(tabs.length > 0 ? tabs[tabs.length - 1] : null);
    }
  }

  public updateActiveTabContent(newContent: string) {
    const current = this.activeTab();
    if (!current) return;

    const isDirty = newContent !== current.originalContent;
    const updated = { ...current, content: newContent, isDirty };

    this.activeTab.set(updated);
    this.openTabs.update((tabs) => tabs.map((t) => (t.id === current.id ? updated : t)));
  }

  public async saveActiveTab() {
    const current = this.activeTab();
    if (!current || !current.isDirty) return;

    const res = await this.bridge.writeFile(current.path, current.content);
    if (res.success) {
      const updated = { ...current, originalContent: current.content, isDirty: false };
      this.activeTab.set(updated);
      this.openTabs.update((tabs) => tabs.map((t) => (t.id === current.id ? updated : t)));
    }
  }

  private detectLanguage(fileName: string): string {
    if (fileName.endsWith('.py')) return 'python';
    if (fileName.endsWith('.ts')) return 'typescript';
    if (fileName.endsWith('.js')) return 'javascript';
    if (fileName.endsWith('.json')) return 'json';
    if (fileName.endsWith('.html')) return 'html';
    if (fileName.endsWith('.css')) return 'css';
    if (fileName.endsWith('.md')) return 'markdown';
    if (fileName.endsWith('.rs')) return 'rust';
    if (fileName.endsWith('.toml') || fileName.endsWith('.yaml') || fileName.endsWith('.yml')) return 'yaml';
    return 'plaintext';
  }

  // --- Task Execution ---
  public async submitTask(instruction: string) {
    this.dirtyWarning.set(null);
    this.activeTask.set({
      instruction,
      status: 'STARTING',
      timeline: [{ id: '1', label: 'Starting task', status: 'active' }],
      is_running: true,
      logs: [`Submitting task: ${instruction}`],
    });

    const res = await this.bridge.startTask(instruction);
    if (!res.success) {
      if (res.error?.code === 'DIRTY_REPOSITORY') {
        this.dirtyWarning.set(res.error.message);
      }
      this.activeTask.update((s) => ({
        ...s,
        is_running: false,
        status: 'FAILED',
        logs: [...s.logs, `Error: ${res.error?.message || 'Unknown error'}`],
      }));
    } else {
      this.activeTask.update((s) => ({
        ...s,
        task_id: res.data.task_id,
        diff: res.data.diff,
        final_answer: res.data.final_answer,
      }));
    }
  }

  public async approveTask() {
    const taskId = this.activeTask().task_id || 'active';
    const res = await this.bridge.approveTask(taskId);
    if (res.success) {
      this.activeTask.update((s) => ({
        ...s,
        status: 'PROMOTED',
        eligible_for_approval: false,
        logs: [...s.logs, 'Changes approved and promoted to base repository.'],
      }));
      this.refreshTasks();
    }
  }

  public async rejectTask() {
    const taskId = this.activeTask().task_id || 'active';
    const res = await this.bridge.rejectTask(taskId);
    if (res.success) {
      this.activeTask.update((s) => ({
        ...s,
        status: 'DECLINED',
        eligible_for_approval: false,
        logs: [...s.logs, 'Changes rejected; isolated worktree discarded.'],
      }));
      this.refreshTasks();
    }
  }

  public async resumeTask(taskId: string) {
    this.activeTask.set({
      task_id: taskId,
      instruction: `Resuming task ${taskId}`,
      status: 'RESUMING',
      timeline: [{ id: 'r1', label: 'Reconciling SQLite and Git checkpoints', status: 'active' }],
      is_running: true,
      logs: [`Resuming task ${taskId}...`],
    });
    this.activeMainView.set('diff');

    const res = await this.bridge.resumeTask(taskId);
    if (!res.success) {
      this.activeTask.update((s) => ({
        ...s,
        is_running: false,
        status: 'FAILED',
        logs: [...s.logs, `Resume failed: ${res.error?.message}`],
      }));
    }
  }

  public async runDoctor() {
    const res = await this.bridge.runDoctor();
    if (res.success) {
      this.doctorReport.set(res.data);
      this.diagnosticsDrawerOpen.set(true);
    }
  }
}
