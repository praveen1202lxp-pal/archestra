import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class BridgeService {
  private eventsSubject = new Subject<{ event: string; data: any }>();
  public events$: Observable<{ event: string; data: any }> = this.eventsSubject.asObservable();
  private eventSource: EventSource | null = null;
  private baseUrl = '';

  constructor() {
    this.initEventSource();
  }

  private initEventSource(): void {
    if (typeof window === 'undefined') return;

    try {
      if (this.eventSource) {
        this.eventSource.close();
      }
      this.eventSource = new EventSource(`${this.baseUrl}/api/events`);
      this.eventSource.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data);
          this.eventsSubject.next(parsed);
        } catch {
          // Ignore non-json lines like pings
        }
      };
      this.eventSource.onerror = () => {
        // Reconnection is handled automatically by browser EventSource
      };
    } catch (err) {
      console.error('SSE initialization error:', err);
    }
  }

  public async getProjectStatus(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/status`);
    return res.json();
  }

  public async openProject(path: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/project/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    return res.json();
  }

  public async listFiles(subpath: string = ''): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/files?subpath=${encodeURIComponent(subpath)}`);
    return res.json();
  }

  public async readFile(filepath: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/file?path=${encodeURIComponent(filepath)}`);
    return res.json();
  }

  public async writeFile(filepath: string, content: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/file`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath, content }),
    });
    return res.json();
  }

  public async startTask(prompt: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, instruction: prompt }),
    });
    return res.json();
  }

  public async getDiff(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/diff`);
    return res.json();
  }

  public async getTestResults(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/tests`);
    return res.json();
  }

  public async getReviewFindings(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/review`);
    return res.json();
  }

  public async approveTask(taskId: string = ''): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    });
    return res.json();
  }

  public async rejectTask(taskId: string = ''): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    });
    return res.json();
  }

  public async listTasks(limit: number = 20): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/history?limit=${limit}`);
    return res.json();
  }

  public async getProviders(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/providers`);
    return res.json();
  }

  public async getProviderStatus(): Promise<any> {
    return this.getProviders();
  }

  public async runDoctor(): Promise<any> {
    const res = await this.getProviders();
    if (res && res.data && res.data.doctor) {
      return { success: true, data: res.data.doctor };
    }
    return res;
  }

  public async resumeTask(taskId: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/task/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    });
    return res.json();
  }

  public async saveConfig(config: any): Promise<any> {
    const res = await fetch(`${this.baseUrl}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config }),
    });
    return res.json();
  }
}
