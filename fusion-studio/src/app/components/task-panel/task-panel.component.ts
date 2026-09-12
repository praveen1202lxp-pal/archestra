import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { StateService } from '../../services/state.service';
import { MonacoEditorComponent } from '../monaco-editor/monaco-editor.component';

@Component({
  selector: 'app-task-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, MonacoEditorComponent],
  template: `
    <div class="task-panel-container">
      <!-- Top Task Input Bar -->
      <div class="task-input-section">
        <div class="input-row">
          <input
            type="text"
            class="task-input"
            placeholder="Enter AI coding task (e.g. Add validation to signup endpoint)..."
            [(ngModel)]="taskInstruction"
            [disabled]="state.activeTask().is_running"
            (keyup.enter)="runTask()"
          />
          <button
            class="run-btn"
            [disabled]="state.activeTask().is_running || !taskInstruction.trim()"
            (click)="runTask()"
          >
            @if (state.activeTask().is_running) {
              <span class="spinner"></span> Running...
            } @else {
              Run Task
            }
          </button>
        </div>

        @if (state.dirtyWarning()) {
          <div class="dirty-banner">
            <span class="banner-icon">⚠️</span>
            <span class="banner-text">{{ state.dirtyWarning() }}</span>
          </div>
        }
      </div>

      <!-- Navigation tabs for task output -->
      <div class="panel-subtabs">
        <button
          class="subtab-btn"
          [class.active]="activeSubView === 'timeline'"
          (click)="activeSubView = 'timeline'"
        >
          Timeline & Stepper
        </button>
        <button
          class="subtab-btn"
          [class.active]="activeSubView === 'diff'"
          (click)="activeSubView = 'diff'"
        >
          Unified Diff
        </button>
        <button
          class="subtab-btn"
          [class.active]="activeSubView === 'tests'"
          (click)="activeSubView = 'tests'"
        >
          Verification Tests
        </button>
        <button
          class="subtab-btn"
          [class.active]="activeSubView === 'review'"
          (click)="activeSubView = 'review'"
        >
          Peer Review Critique
        </button>
      </div>

      <!-- Content Area -->
      <div class="task-content-body">
        <!-- Timeline View -->
        @if (activeSubView === 'timeline') {
          <div class="timeline-view">
            @if (state.activeTask().timeline.length === 0) {
              <div class="empty-prompt">
                <p>No task currently executing.</p>
                <p class="hint">Type an instruction above and click <strong>Run Task</strong>.</p>
              </div>
            } @else {
              <div class="stepper">
                @for (step of state.activeTask().timeline; track step.id) {
                  <div class="step-item" [class]="step.status">
                    <div class="step-bullet">
                      @if (step.status === 'completed') {
                        ✓
                      } @else if (step.status === 'active') {
                        ●
                      } @else if (step.status === 'failed') {
                        ✗
                      } @else {
                        ○
                      }
                    </div>
                    <div class="step-content">
                      <div class="step-label">{{ step.label }}</div>
                      @if (step.detail) {
                        <div class="step-detail">{{ step.detail }}</div>
                      }
                    </div>
                  </div>
                }
              </div>

              @if (state.activeTask().final_answer) {
                <div class="summary-box">
                  <div class="summary-header">Fusion Result:</div>
                  <div class="summary-text">{{ state.activeTask().final_answer }}</div>
                </div>
              }
            }
          </div>
        }

        <!-- Diff View -->
        @if (activeSubView === 'diff') {
          <div class="diff-view">
            @if (!state.activeTask().diff) {
              <div class="empty-prompt">No unified diff generated yet.</div>
            } @else {
              <pre class="diff-text">{{ state.activeTask().diff }}</pre>
            }
          </div>
        }

        <!-- Tests View -->
        @if (activeSubView === 'tests') {
          <div class="tests-view">
            @if (!state.activeTask().verification) {
              <div class="empty-prompt">No verification run in this session.</div>
            } @else {
              <div class="test-header" [class.passed]="state.activeTask().verification?.passed">
                <h3>
                  {{ state.activeTask().verification?.passed ? '✓ Verification PASSED' : '✗ Verification FAILED' }}
                </h3>
                <span class="test-meta">
                  Exit Code: {{ state.activeTask().verification?.exit_code }} | Duration: {{ state.activeTask().verification?.duration_seconds }}s
                </span>
              </div>
              @if (state.activeTask().verification?.stderr) {
                <pre class="test-output stderr">{{ state.activeTask().verification?.stderr }}</pre>
              }
              @if (state.activeTask().verification?.stdout) {
                <pre class="test-output">{{ state.activeTask().verification?.stdout }}</pre>
              }
            }
          </div>
        }

        <!-- Review View -->
        @if (activeSubView === 'review') {
          <div class="review-view">
            @if (!state.activeTask().review) {
              <div class="empty-prompt">No cross-model peer review recorded in this session.</div>
            } @else {
              <div class="review-card">
                <div class="review-header">
                  <span class="review-status" [class.approved]="state.activeTask().review?.status === 'APPROVED'">
                    [{{ state.activeTask().review?.status }}]
                  </span>
                  <span class="reviewer-name">Reviewer: {{ state.activeTask().review?.reviewer_agent }}</span>
                </div>
                <div class="review-comments">
                  {{ state.activeTask().review?.comments }}
                </div>
              </div>
            }
          </div>
        }
      </div>

      <!-- Human Promotion Approval Gate -->
      @if (state.activeTask().eligible_for_approval) {
        <div class="promotion-gate-bar">
          <div class="gate-info">
            <strong>Human Promotion Gate:</strong> Verified unified diff ready for inspection.
          </div>
          <div class="gate-actions">
            <button class="reject-btn" (click)="rejectChanges()">Reject</button>
            <button class="approve-btn" (click)="approveChanges()">Approve Changes</button>
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .task-panel-container {
        display: flex;
        flex-direction: column;
        height: 100%;
        background-color: #1a1a1a;
        color: #d4d4d4;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .task-input-section {
        padding: 10px 14px;
        background-color: #222222;
        border-bottom: 1px solid #2e2e2e;
      }
      .input-row {
        display: flex;
        gap: 8px;
      }
      .task-input {
        flex: 1;
        background-color: #2a2a2a;
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 8px 12px;
        color: #ffffff;
        font-size: 13px;
        outline: none;
      }
      .task-input:focus {
        border-color: #0e639c;
      }
      .run-btn {
        background-color: #0e639c;
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 8px 18px;
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .run-btn:disabled {
        background-color: #383838;
        color: #777777;
        cursor: not-allowed;
      }
      .run-btn:not(:disabled):hover {
        background-color: #1177bb;
      }
      .dirty-banner {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 8px;
        padding: 8px 12px;
        background-color: #3a2e18;
        border: 1px solid #8c6a28;
        border-radius: 4px;
        color: #f0c050;
        font-size: 12px;
      }
      .panel-subtabs {
        display: flex;
        background-color: #1e1e1e;
        border-bottom: 1px solid #2e2e2e;
        padding: 0 8px;
      }
      .subtab-btn {
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        color: #999999;
        padding: 8px 14px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
      }
      .subtab-btn:hover {
        color: #cccccc;
      }
      .subtab-btn.active {
        color: #ffffff;
        border-bottom-color: #0e639c;
      }
      .task-content-body {
        flex: 1;
        overflow-y: auto;
        padding: 14px;
      }
      .stepper {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .step-item {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        font-size: 13px;
      }
      .step-bullet {
        width: 20px;
        height: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: bold;
        border-radius: 50%;
      }
      .step-item.completed .step-bullet {
        color: #4ec9b0;
      }
      .step-item.active .step-bullet {
        color: #007acc;
        animation: pulse 1s infinite alternate;
      }
      .step-item.failed .step-bullet {
        color: #f14c4c;
      }
      .step-item.pending .step-bullet {
        color: #666666;
      }
      .step-label {
        font-weight: 500;
        color: #e0e0e0;
      }
      .step-detail {
        font-size: 11px;
        color: #888888;
        margin-top: 2px;
      }
      .summary-box {
        margin-top: 20px;
        padding: 12px;
        background-color: #23272e;
        border-left: 3px solid #4ec9b0;
        border-radius: 2px;
      }
      .summary-header {
        font-weight: 600;
        color: #4ec9b0;
        font-size: 12px;
        margin-bottom: 6px;
      }
      .summary-text {
        font-size: 13px;
        line-height: 1.4;
      }
      .diff-text {
        font-family: Consolas, monospace;
        font-size: 12px;
        background-color: #141414;
        padding: 12px;
        border-radius: 4px;
        overflow-x: auto;
        color: #ce9178;
      }
      .promotion-gate-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 18px;
        background-color: #20252b;
        border-top: 1px solid #333a44;
      }
      .gate-info {
        font-size: 13px;
      }
      .gate-actions {
        display: flex;
        gap: 10px;
      }
      .reject-btn {
        background-color: #5a1d1d;
        color: #ff8888;
        border: 1px solid #7a2626;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        font-weight: 500;
      }
      .reject-btn:hover {
        background-color: #7a2626;
      }
      .approve-btn {
        background-color: #1e6338;
        color: #88ffaa;
        border: 1px solid #287a46;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        font-weight: 500;
      }
      .approve-btn:hover {
        background-color: #287a46;
      }
      .test-header {
        padding: 10px;
        border-radius: 4px;
        background-color: #2b1d1d;
        margin-bottom: 10px;
      }
      .test-header.passed {
        background-color: #1d2b20;
      }
      .test-output {
        background-color: #141414;
        padding: 10px;
        font-family: monospace;
        font-size: 11px;
        border-radius: 4px;
        max-height: 250px;
        overflow-y: auto;
      }
      .test-output.stderr {
        color: #f14c4c;
      }
      .review-card {
        background-color: #222222;
        padding: 14px;
        border-radius: 4px;
      }
      .review-header {
        display: flex;
        gap: 10px;
        font-size: 13px;
        font-weight: 600;
        margin-bottom: 8px;
      }
      .review-status.approved {
        color: #4ec9b0;
      }
      .review-comments {
        font-size: 13px;
        line-height: 1.4;
        color: #cccccc;
      }
      .empty-prompt {
        padding: 30px;
        text-align: center;
        color: #777777;
        font-size: 13px;
      }
      .hint {
        font-size: 12px;
        color: #555555;
        margin-top: 4px;
      }
      @keyframes pulse {
        from { opacity: 0.5; }
        to { opacity: 1; }
      }
    `,
  ],
})
export class TaskPanelComponent implements OnInit {
  public taskInstruction: string = '';
  public activeSubView: 'timeline' | 'diff' | 'tests' | 'review' = 'timeline';

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
}
