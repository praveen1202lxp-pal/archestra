import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { StateService } from '../../services/state.service';
import { FileItem } from '../../models/studio.models';

@Component({
  selector: 'app-project-explorer',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="explorer-container">
      <div class="explorer-header">
        <span class="header-title">EXPLORER</span>
        <button class="icon-btn" (click)="refresh()" title="Refresh Explorer">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10"></polyline>
            <polyline points="1 20 1 14 7 14"></polyline>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
        </button>
      </div>

      <div class="project-tree">
        <div class="project-name-bar">
          <span class="project-label">▾ {{ state.project().name }}</span>
        </div>

        <div class="file-list">
          @for (item of state.files(); track item.path) {
            <div
              class="tree-item"
              [class.is-dir]="item.is_dir"
              [class.active]="state.activeTab()?.path === item.path"
              (click)="onItemClick(item)"
            >
              <span class="item-icon">
                @if (item.is_dir) {
                  📁
                } @else if (item.name.endsWith('.py')) {
                  🐍
                } @else if (item.name.endsWith('.ts') || item.name.endsWith('.js')) {
                  📜
                } @else if (item.name.endsWith('.json')) {
                  ⚙️
                } @else if (item.name.endsWith('.md')) {
                  📝
                } @else {
                  📄
                }
              </span>
              <span class="item-name">{{ item.name }}</span>
            </div>
          }
          @if (state.files().length === 0) {
            <div class="empty-state">No files loaded</div>
          }
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .explorer-container {
        display: flex;
        flex-direction: column;
        height: 100%;
        background-color: #181818;
        color: #cccccc;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px;
        user-select: none;
      }
      .explorer-header {
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
      .project-tree {
        flex: 1;
        overflow-y: auto;
      }
      .project-name-bar {
        padding: 6px 12px;
        font-weight: 600;
        color: #dddddd;
        font-size: 12px;
        background-color: #202020;
      }
      .file-list {
        padding: 4px 0;
      }
      .tree-item {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 16px;
        cursor: pointer;
        transition: background-color 0.1s;
      }
      .tree-item:hover {
        background-color: #2a2d2e;
      }
      .tree-item.active {
        background-color: #37373d;
        color: #ffffff;
      }
      .item-icon {
        font-size: 13px;
      }
      .item-name {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .empty-state {
        padding: 16px;
        text-align: center;
        color: #666666;
        font-style: italic;
      }
    `,
  ],
})
export class ProjectExplorerComponent implements OnInit {
  constructor(public state: StateService) {}

  ngOnInit(): void {}

  public refresh(): void {
    this.state.refreshFiles();
  }

  public onItemClick(item: FileItem): void {
    if (!item.is_dir) {
      this.state.openFile(item.path);
    }
  }
}
