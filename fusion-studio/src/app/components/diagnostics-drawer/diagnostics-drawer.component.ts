import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { StateService } from '../../services/state.service';

@Component({
  selector: 'app-diagnostics-drawer',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="drawer-overlay" (click)="close()">
      <div class="drawer-content" (click)="$event.stopPropagation()">
        <div class="drawer-header">
          <h2>System Diagnostics & Provider Health</h2>
          <button class="close-btn" (click)="close()">✕</button>
        </div>

        <div class="drawer-body">
          <!-- Providers Section -->
          <div class="section">
            <h3>Configured LLM Providers</h3>
            <div class="provider-cards">
              @for (p of state.providers(); track p.id) {
                <div class="p-card" [class.healthy]="p.healthy" [class.unhealthy]="!p.healthy">
                  <div class="p-top">
                    <span class="p-name">{{ p.name }}</span>
                    <span class="p-status">{{ p.healthy ? 'READY' : 'UNAVAILABLE' }}</span>
                  </div>
                  <div class="p-detail">
                    <div><strong>Type:</strong> {{ p.type }}</div>
                    @if (p.model) {
                      <div><strong>Model:</strong> {{ p.model }}</div>
                    }
                    <div><strong>Latency:</strong> {{ p.latency_ms }} ms</div>
                    <div class="p-msg">{{ p.message }}</div>
                  </div>
                </div>
              }
            </div>
          </div>

          <!-- Doctor Report Section -->
          <div class="section">
            <div class="section-title-row">
              <h3>System Environment (fusion doctor)</h3>
              <button class="run-doctor-btn" (click)="runDoctor()">Run Diagnostics</button>
            </div>

            @if (state.doctorReport(); as report) {
              <div class="report-summary">
                Overall:
                <span [class.green]="report.is_healthy" [class.red]="!report.is_healthy">
                  {{ report.is_healthy ? 'HEALTHY' : 'ISSUES DETECTED' }}
                </span>
                ({{ report.passed_count }} passed, {{ report.warn_count }} warnings, {{ report.fail_count }} failures)
              </div>

              <div class="checks-list">
                @for (c of report.checks; track c.name) {
                  <div class="check-item" [class]="c.status.toLowerCase()">
                    <div class="check-top">
                      <span class="check-badge">{{ c.status }}</span>
                      <span class="check-name">{{ c.name }}</span>
                    </div>
                    <div class="check-msg">{{ c.message }}</div>
                    @if (c.remediation) {
                      <div class="check-remediation">Remediation: {{ c.remediation }}</div>
                    }
                  </div>
                }
              </div>
            } @else {
              <div class="empty-prompt">Click "Run Diagnostics" to execute non-generative doctor checks.</div>
            }
          </div>

          <!-- Trust & Privacy Boundary Notice -->
          <div class="privacy-notice">
            🔒 <strong>Secret Masking:</strong> Sensitive tokens, credentials, and API keys are automatically sanitized and never rendered in the UI. Worktree isolation provides repository isolation, not an OS sandbox.
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .drawer-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: rgba(0, 0, 0, 0.6);
        z-index: 1000;
        display: flex;
        justify-content: flex-end;
      }
      .drawer-content {
        width: 500px;
        background-color: #1e1e1e;
        color: #cccccc;
        height: 100%;
        display: flex;
        flex-direction: column;
        box-shadow: -4px 0 16px rgba(0, 0, 0, 0.5);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .drawer-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px 20px;
        border-bottom: 1px solid #2e2e2e;
      }
      .drawer-header h2 {
        font-size: 15px;
        font-weight: 600;
        margin: 0;
        color: #ffffff;
      }
      .close-btn {
        background: transparent;
        border: none;
        color: #888888;
        font-size: 16px;
        cursor: pointer;
      }
      .close-btn:hover {
        color: #ffffff;
      }
      .drawer-body {
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 20px;
      }
      .section h3 {
        font-size: 13px;
        font-weight: 600;
        color: #999999;
        text-transform: uppercase;
        margin: 0 0 10px 0;
      }
      .section-title-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 10px;
      }
      .run-doctor-btn {
        background-color: #0e639c;
        color: #ffffff;
        border: none;
        border-radius: 3px;
        padding: 4px 10px;
        font-size: 11px;
        cursor: pointer;
      }
      .provider-cards {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .p-card {
        padding: 12px;
        background-color: #252526;
        border-left: 3px solid #666666;
        border-radius: 4px;
      }
      .p-card.healthy {
        border-left-color: #4ec9b0;
      }
      .p-card.unhealthy {
        border-left-color: #f14c4c;
      }
      .p-top {
        display: flex;
        justify-content: space-between;
        font-weight: 600;
        margin-bottom: 6px;
      }
      .p-status {
        font-size: 11px;
      }
      .p-detail {
        font-size: 12px;
        color: #aaaaaa;
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .p-msg {
        color: #888888;
        font-size: 11px;
        margin-top: 4px;
      }
      .report-summary {
        font-size: 12px;
        margin-bottom: 10px;
        padding: 8px;
        background-color: #252526;
        border-radius: 4px;
      }
      .report-summary .green { color: #4ec9b0; font-weight: bold; }
      .report-summary .red { color: #f14c4c; font-weight: bold; }
      .checks-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .check-item {
        padding: 10px;
        background-color: #252526;
        border-radius: 4px;
        font-size: 12px;
      }
      .check-top {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
      }
      .check-badge {
        font-size: 10px;
        font-weight: bold;
        padding: 2px 5px;
        border-radius: 3px;
      }
      .check-item.pass .check-badge { background-color: #1d3b24; color: #4ec9b0; }
      .check-item.warn .check-badge { background-color: #3b351d; color: #dcdcaa; }
      .check-item.fail .check-badge { background-color: #3b1d1d; color: #f14c4c; }
      .check-name { font-weight: 600; color: #ffffff; }
      .check-msg { color: #aaaaaa; }
      .check-remediation { color: #dcdcaa; font-size: 11px; margin-top: 4px; }
      .privacy-notice {
        font-size: 11px;
        color: #888888;
        padding: 12px;
        background-color: #1a1a1a;
        border: 1px solid #282828;
        border-radius: 4px;
        line-height: 1.4;
      }
      .empty-prompt {
        font-size: 12px;
        color: #777777;
        text-align: center;
        padding: 20px;
      }
    `,
  ],
})
export class DiagnosticsDrawerComponent {
  constructor(public state: StateService) {}

  public close(): void {
    this.state.diagnosticsDrawerOpen.set(false);
  }

  public runDoctor(): void {
    this.state.runDoctor();
  }
}
