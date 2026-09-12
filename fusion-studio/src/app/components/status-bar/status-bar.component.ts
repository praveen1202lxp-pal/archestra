import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { StateService } from '../../services/state.service';

@Component({
  selector: 'app-status-bar',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="status-bar-container">
      <div class="status-left">
        <span class="status-item brand">Fusion Studio v{{ state.project().version }}</span>
        <span class="status-item mode">{{ state.project().optimization_mode }}</span>
        <span class="status-item git-status" [class.dirty]="!state.project().git_clean">
          Git: {{ state.project().git_clean ? 'Clean' : 'Modified' }}
        </span>
      </div>

      <div class="status-right">
        @for (p of state.providers(); track p.id) {
          <span class="status-item provider" [class.healthy]="p.healthy" [class.unhealthy]="!p.healthy">
            {{ p.name.split(' ')[1] || p.id }} {{ p.healthy ? '✓' : '✗' }}
          </span>
        }
        <button class="status-btn" (click)="toggleDiagnostics()">
          Diagnostics
        </button>
      </div>
    </div>
  `,
  styles: [
    `
      .status-bar-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        height: 24px;
        background-color: #007acc;
        color: #ffffff;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 11px;
        padding: 0 10px;
        user-select: none;
      }
      .status-left, .status-right {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .status-item {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .status-item.brand {
        font-weight: 600;
      }
      .status-item.git-status.dirty {
        color: #ffe066;
        font-weight: bold;
      }
      .status-item.provider.unhealthy {
        color: #ff9999;
      }
      .status-btn {
        background: rgba(255, 255, 255, 0.2);
        border: none;
        color: #ffffff;
        border-radius: 2px;
        padding: 2px 6px;
        font-size: 10px;
        cursor: pointer;
      }
      .status-btn:hover {
        background: rgba(255, 255, 255, 0.35);
      }
    `,
  ],
})
export class StatusBarComponent {
  constructor(public state: StateService) {}

  public toggleDiagnostics(): void {
    this.state.diagnosticsDrawerOpen.update((v) => !v);
  }
}
