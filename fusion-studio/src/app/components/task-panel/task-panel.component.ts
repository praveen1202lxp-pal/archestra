import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { StateService } from '../../services/state.service';

@Component({
  selector: 'app-task-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ask-fusion-panel">
      <!-- Section 1: Ask Fusion Box -->
      <div class="panel-section ask-box">
        <h3 class="section-title">Ask Fusion</h3>
        <textarea
          class="prompt-textarea"
          placeholder="What should Fusion build or change in this repository?"
          [(ngModel)]="taskInstruction"
          [disabled]="state.activeTask().is_running"
          (keydown.ctrl.enter)="runTask()"
          rows="4"
        ></textarea>

        <div class="action-row">
          <span class="hint-text">Ctrl+Enter to run</span>
          <button
            class="primary-run-btn"
            [disabled]="state.activeTask().is_running || !taskInstruction.trim()"
            (click)="runTask()"
          >
            @if (state.activeTask().is_running) {
              <span class="spinner"></span> Working...
            } @else {
              Run Task
            }
          </button>
        </div>

        @if (state.dirtyWarning()) {
          <div class="dirty-alert">
            <span class="alert-icon">⚠️</span>
            <div class="alert-msg">{{ state.dirtyWarning() }}</div>
          </div>
        }
      </div>

      <!-- Section 2: Simple Execution Status -->
      <div class="panel-section status-box">
        <div class="status-header">
          <span class="status-label">Status:</span>
          <span
            class="status-badge"
            [class.running]="state.activeTask().is_running"
            [class.completed]="state.activeTask().status === 'COMPLETED' || state.activeTask().status === 'AWAITING_APPROVAL'"
            [class.failed]="state.activeTask().status === 'FAILED'"
          >
            {{ getDisplayStatus() }}
          </span>
        </div>

        @if (state.activeTask().is_running) {
          <div class="live-progress">
            <div class="pulse-dot"></div>
            <div class="progress-message">{{ getLatestProgress() }}</div>
          </div>
        }

        @if (state.activeTask().final_answer) {
          <div class="summary-card">
            {{ state.activeTask().final_answer }}
          </div>
        }
      </div>

      <!-- Section 3: Tests Result -->
      @if (state.activeTask().verification) {
        <div class="panel-section tests-box">
          <div
            class="test-result-bar"
            [class.passed]="state.activeTask().verification?.passed"
            [class.failed]="!state.activeTask().verification?.passed"
          >
            <span class="test-icon">{{ state.activeTask().verification?.passed ? '✓' : '✗' }}</span>
            <span class="test-title">
              {{ state.activeTask().verification?.passed ? 'Verification Passed' : 'Verification Failed' }}
            </span>
            <span class="test-duration">
              ({{ state.activeTask().verification?.duration_seconds }}s)
            </span>
          </div>

          @if (state.activeTask().verification?.stderr) {
            <pre class="test-snippet error">{{ state.activeTask().verification?.stderr }}</pre>
          } @else if (state.activeTask().verification?.stdout) {
            <pre class="test-snippet">{{ state.activeTask().verification?.stdout }}</pre>
          }
        </div>
      }

      <!-- Section 4: Diff Viewer & Review Findings -->
      @if (state.activeTask().diff) {
        <div class="panel-section diff-box">
          <div class="diff-header">
            <span class="section-title">Candidate Changes</span>
            <button
              class="inspect-diff-btn"
              (click)="state.activeMainView.set('diff')"
            >
              ⚖️ View Diff
            </button>
          </div>

          @if (state.activeTask().review) {
            <div class="review-status-card">
              <div class="review-status-line">
                <span class="review-tag" [class.approved]="state.activeTask().review?.status === 'APPROVED'">
                  Peer Review: {{ state.activeTask().review?.status }}
                </span>
              </div>
              <div class="review-note">
                {{ state.activeTask().review?.comments }}
              </div>
            </div>
          }
        </div>
      }

      <!-- Section 5: Approve / Reject Action Bar -->
      @if (state.activeTask().eligible_for_approval) {
        <div class="approval-action-bar">
          <div class="approval-heading">Review Candidate Changes</div>
          <div class="approval-buttons">
            <button class="reject-button" (click)="rejectChanges()">
              Reject
            </button>
            <button class="approve-button" (click)="approveChanges()">
              Approve Changes
            </button>
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .ask-fusion-panel {
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 14px;
        background-color: #191919;
        height: 100%;
        box-sizing: border-box;
        overflow-y: auto;
        color: #d4d4d4;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .panel-section {
        background-color: #202020;
        border: 1px solid #2e2e2e;
        border-radius: 6px;
        padding: 12px;
      }
      .section-title {
        margin: 0 0 8px 0;
        font-size: 13px;
        font-weight: 600;
        color: #ffffff;
      }
      .prompt-textarea {
        width: 100%;
        box-sizing: border-box;
        background-color: #292929;
        border: 1px solid #3d3d3d;
        border-radius: 4px;
        color: #ffffff;
        font-family: inherit;
        font-size: 13px;
        padding: 10px;
        resize: vertical;
        outline: none;
      }
      .prompt-textarea:focus {
        border-color: #0e639c;
      }
      .action-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 8px;
      }
      .hint-text {
        font-size: 11px;
        color: #777777;
      }
      .primary-run-btn {
        background-color: #0e639c;
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .primary-run-btn:disabled {
        background-color: #383838;
        color: #777777;
        cursor: not-allowed;
      }
      .primary-run-btn:not(:disabled):hover {
        background-color: #1177bb;
      }
      .dirty-alert {
        margin-top: 10px;
        padding: 8px 10px;
        background-color: #382c16;
        border: 1px solid #7a5c20;
        border-radius: 4px;
        color: #f1c40f;
        font-size: 12px;
        display: flex;
        gap: 8px;
      }
      .status-header {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .status-label {
        font-size: 12px;
        color: #888888;
      }
      .status-badge {
        font-size: 12px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 3px;
        background-color: #2d2d2d;
        color: #aaaaaa;
      }
      .status-badge.running {
        background-color: #1b3d54;
        color: #58a6ff;
      }
      .status-badge.completed {
        background-color: #1a3d24;
        color: #56d364;
      }
      .status-badge.failed {
        background-color: #4a1e1e;
        color: #f85149;
      }
      .live-progress {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 10px;
        padding: 8px 10px;
        background-color: #18222a;
        border-radius: 4px;
        font-size: 12px;
        color: #58a6ff;
      }
      .pulse-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background-color: #58a6ff;
        animation: pulse 1s infinite alternate;
      }
      .summary-card {
        margin-top: 8px;
        font-size: 12px;
        line-height: 1.4;
        color: #cccccc;
        background-color: #1a1a1a;
        padding: 8px;
        border-radius: 4px;
      }
      .test-result-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        font-weight: 600;
        padding: 6px 10px;
        border-radius: 4px;
      }
      .test-result-bar.passed {
        background-color: #1b3824;
        color: #56d364;
      }
      .test-result-bar.failed {
        background-color: #3d1c1c;
        color: #f85149;
      }
      .test-duration {
        font-size: 11px;
        font-weight: normal;
        color: #888888;
      }
      .test-snippet {
        margin: 8px 0 0 0;
        padding: 8px;
        background-color: #141414;
        font-family: monospace;
        font-size: 11px;
        border-radius: 4px;
        max-height: 120px;
        overflow-y: auto;
        color: #cccccc;
      }
      .test-snippet.error {
        color: #f85149;
      }
      .diff-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .inspect-diff-btn {
        background-color: #2b2b2b;
        color: #cccccc;
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 11px;
        cursor: pointer;
      }
      .inspect-diff-btn:hover {
        background-color: #383838;
        color: #ffffff;
      }
      .review-status-card {
        margin-top: 8px;
        background-color: #191919;
        padding: 8px;
        border-radius: 4px;
      }
      .review-tag {
        font-size: 11px;
        font-weight: 600;
        color: #e3b341;
      }
      .review-tag.approved {
        color: #56d364;
      }
      .review-note {
        font-size: 11px;
        color: #aaaaaa;
        margin-top: 4px;
        line-height: 1.3;
      }
      .approval-action-bar {
        background-color: #1e2621;
        border: 1px solid #2e4d35;
        border-radius: 6px;
        padding: 12px;
        margin-top: auto;
      }
      .approval-heading {
        font-size: 12px;
        font-weight: 600;
        color: #56d364;
        margin-bottom: 10px;
        text-align: center;
      }
      .approval-buttons {
        display: flex;
        gap: 10px;
      }
      .reject-button {
        flex: 1;
        background-color: #4a1e1e;
        color: #ff8888;
        border: 1px solid #732626;
        border-radius: 4px;
        padding: 8px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
      }
      .reject-button:hover {
        background-color: #632323;
      }
      .approve-button {
        flex: 1;
        background-color: #1f6b3b;
        color: #ffffff;
        border: 1px solid #2a8a4c;
        border-radius: 4px;
        padding: 8px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
      }
      .approve-button:hover {
        background-color: #288449;
      }
      .spinner {
        display: inline-block;
        width: 12px;
        height: 12px;
        border: 2px solid rgba(255,255,255,0.3);
        border-top-color: #ffffff;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }
      @keyframes spin {
        to { transform: rotate(360deg); }
      }
      @keyframes pulse {
        from { opacity: 0.4; }
        to { opacity: 1; }
      }
    `,
  ],
})
export class TaskPanelComponent implements OnInit {
  public taskInstruction: string = '';

  constructor(public state: StateService) {}

  ngOnInit(): void {}

  public runTask(): void {
    if (!this.taskInstruction.trim() || this.state.activeTask().is_running) return;
    this.state.submitTask(this.taskInstruction.trim());
  }

  public approveChanges(): void {
    this.state.approveTask();
  }

  public rejectChanges(): void {
    this.state.rejectTask();
  }

  public getDisplayStatus(): string {
    const task = this.state.activeTask();
    if (task.is_running) return 'Running';
    if (task.status === 'AWAITING_APPROVAL') return 'Review Ready';
    if (task.status === 'COMPLETED') return 'Completed';
    if (task.status === 'FAILED') return 'Failed';
    if (task.status === 'PROMOTED') return 'Approved';
    if (task.status === 'DECLINED') return 'Rejected';
    return 'Idle';
  }

  public getLatestProgress(): string {
    const logs = this.state.activeTask().logs;
    if (logs.length === 0) return 'Analyzing task...';
    return logs[logs.length - 1];
  }
}
