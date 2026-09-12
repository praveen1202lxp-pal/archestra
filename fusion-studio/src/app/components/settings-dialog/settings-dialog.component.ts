import { Component, EventEmitter, OnInit, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { StateService } from '../../services/state.service';
import { BridgeService } from '../../services/bridge.service';

@Component({
  selector: 'app-settings-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-overlay" (click)="close.emit()">
      <div class="modal-card" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h2>Project Configuration & Settings</h2>
          <button class="close-btn" (click)="close.emit()">✕</button>
        </div>

        <div class="modal-body">
          <div class="form-group">
            <label>Optimization Mode</label>
            <select [(ngModel)]="mode" class="form-control">
              <option value="FAST">FAST (Single agent fast-path)</option>
              <option value="BALANCED">BALANCED (Autonomous edit with peer review)</option>
              <option value="QUALITY">QUALITY (Full multi-agent deliberation & repair)</option>
            </select>
            <span class="help-text">Controls task assessment thresholds and deliberation depth.</span>
          </div>

          <div class="form-group">
            <label>Automated Verification Command</label>
            <input type="text" [(ngModel)]="verificationCmd" class="form-control" placeholder="pytest" />
            <span class="help-text">Test suite command executed natively in isolated Git worktrees.</span>
          </div>

          <div class="form-group">
            <label>Log Level</label>
            <select [(ngModel)]="logLevel" class="form-control">
              <option value="INFO">INFO</option>
              <option value="DEBUG">DEBUG (Detailed deliberation traces)</option>
              <option value="WARNING">WARNING</option>
            </select>
          </div>

          <div class="security-box">
            🛡️ <strong>Precedence & Secrets:</strong> Settings are saved to <code>.fusion/config.json</code>. Sensitive API keys or executable paths remain in user-global config or environment variables and are never committed here.
          </div>
        </div>

        <div class="modal-footer">
          <button class="btn btn-secondary" (click)="close.emit()">Cancel</button>
          <button class="btn btn-primary" (click)="save()">Save Settings</button>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .modal-overlay {
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background-color: rgba(0, 0, 0, 0.65);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .modal-card {
        width: 520px;
        background-color: #252526;
        color: #cccccc;
        border-radius: 6px;
        border: 1px solid #3c3c3c;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        overflow: hidden;
      }
      .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 14px 20px;
        background-color: #1e1e1e;
        border-bottom: 1px solid #333333;
      }
      .modal-header h2 {
        font-size: 14px;
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
      .modal-body {
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .form-group {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .form-group label {
        font-size: 12px;
        font-weight: 600;
        color: #dddddd;
      }
      .form-control {
        background-color: #1a1a1a;
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 8px 10px;
        color: #ffffff;
        font-size: 12px;
        outline: none;
      }
      .form-control:focus {
        border-color: #0e639c;
      }
      .help-text {
        font-size: 11px;
        color: #888888;
      }
      .security-box {
        font-size: 11px;
        color: #aaaaaa;
        background-color: #1e1e1e;
        padding: 10px;
        border-radius: 4px;
        border-left: 3px solid #0e639c;
        line-height: 1.4;
      }
      .security-box code {
        color: #4ec9b0;
      }
      .modal-footer {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        padding: 14px 20px;
        background-color: #1e1e1e;
        border-top: 1px solid #333333;
      }
      .btn {
        padding: 6px 16px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        border: none;
      }
      .btn-secondary {
        background-color: #3a3d41;
        color: #cccccc;
      }
      .btn-primary {
        background-color: #0e639c;
        color: #ffffff;
      }
      .btn-primary:hover {
        background-color: #1177bb;
      }
    `,
  ],
})
export class SettingsDialogComponent implements OnInit {
  @Output() close = new EventEmitter<void>();

  public mode: string = 'BALANCED';
  public verificationCmd: string = 'pytest';
  public logLevel: string = 'INFO';

  constructor(public state: StateService, private bridge: BridgeService) {}

  ngOnInit(): void {
    this.mode = this.state.project().optimization_mode || 'BALANCED';
    this.verificationCmd = this.state.project().verification_command || 'pytest';
  }

  public async save(): Promise<void> {
    const configUpdate = {
      optimization_mode: this.mode,
      verification_command: this.verificationCmd,
    };
    await this.bridge.saveConfig(configUpdate);
    this.state.project.update((p) => ({
      ...p,
      optimization_mode: this.mode,
      verification_command: this.verificationCmd,
    }));
    this.close.emit();
  }
}
