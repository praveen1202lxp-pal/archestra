import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { StateService } from '../../services/state.service';
import { TaskSummary } from '../../models/studio.models';

@Component({
  selector: 'app-history-panel',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="history-container">
      <div class="history-header">
        <span class="header-title">TASK HISTORY</span>
        <button class="icon-btn" (click)="refresh()" title="Refresh Tasks">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10"></polyline>
            <polyline points="1 20 1 14 7 14"></polyline>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
        </button>
      </div>

      <div class="task-list">
        @for (t of state.taskHistory(); track t.id) {
          <div class="task-item" [class.selected]="selectedTaskId === t.id" (click)="selectedTaskId = t.id">
            <div class="task-row-top">
              <span class="task-id">#{{ t.id }}</span>
              <span class="status-badge" [class]="t.status.toLowerCase()">
                {{ t.status }}
              </span>
            </div>
            <div class="task-title">{{ t.title }}</div>
            <div class="task-footer">
              <span class="task-time">{{ formatTime(t.created_at) }}</span>
              @if (t.recoverable) {
                <button class="resume-btn" (click)="resume(t.id); $event.stopPropagation()">
                  Resume
                </button>
              }
            </div>
          </div>
        }
        @if (state.taskHistory().length === 0) {
          <div class="empty-state">No recorded tasks in project SQLite brain.</div>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .history-container {
        display: flex;
        flex-direction: column;
        height: 100%;
        background-color: #181818;
        color: #cccccc;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px;
      }
      .history-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 12px;
        border-bottom: 1px solid #282828;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.5px;
        color: #999999;
      }
      .icon-btn {
        background: transparent;
        border: none;
        color: #888888;
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 3px;
      }
      .icon-btn:hover {
        color: #ffffff;
        background-color: #333333;
      }
      .task-list {
        flex: 1;
        overflow-y: auto;
        padding: 6px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .task-item {
        padding: 8px 10px;
        background-color: #202020;
        border: 1px solid #2a2a2a;
        border-radius: 4px;
        cursor: pointer;
        transition: background-color 0.1s;
      }
      .task-item:hover {
        background-color: #282828;
      }
      .task-item.selected {
        border-color: #0e639c;
        background-color: #262a30;
      }
      .task-row-top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 4px;
      }
      .task-id {
        font-family: monospace;
        font-size: 11px;
        color: #888888;
      }
      .status-badge {
        font-size: 10px;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 3px;
        text-transform: uppercase;
      }
      .status-badge.completed {
        background-color: #1d3b24;
        color: #4ec9b0;
      }
      .status-badge.failed {
        background-color: #3b1d1d;
        color: #f14c4c;
      }
      .status-badge.recoverable,
      .status-badge.interrupted {
        background-color: #3b351d;
        color: #dcdcaa;
      }
      .task-title {
        font-size: 12px;
        font-weight: 500;
        color: #eeeeee;
        margin-bottom: 6px;
        line-height: 1.3;
      }
      .task-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .task-time {
        font-size: 10px;
        color: #777777;
      }
      .resume-btn {
        background-color: #0e639c;
        color: #ffffff;
        border: none;
        border-radius: 3px;
        padding: 3px 8px;
        font-size: 10px;
        font-weight: 600;
        cursor: pointer;
      }
      .resume-btn:hover {
        background-color: #1177bb;
      }
      .empty-state {
        padding: 20px;
        text-align: center;
        color: #666666;
        font-size: 12px;
      }
    `,
  ],
})
export class HistoryPanelComponent {
  public selectedTaskId: string | null = null;

  constructor(public state: StateService) {}

  public refresh(): void {
    this.state.refreshTasks();
  }

  public resume(taskId: string): void {
    this.state.resumeTask(taskId);
  }

  public formatTime(iso: string): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return iso;
    }
  }
}
