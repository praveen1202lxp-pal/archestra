import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { StateService } from './services/state.service';
import { ProjectExplorerComponent } from './components/project-explorer/project-explorer.component';
import { HistoryPanelComponent } from './components/history-panel/history-panel.component';
import { MonacoEditorComponent } from './components/monaco-editor/monaco-editor.component';
import { TaskPanelComponent } from './components/task-panel/task-panel.component';
import { DiagnosticsDrawerComponent } from './components/diagnostics-drawer/diagnostics-drawer.component';
import { SettingsDialogComponent } from './components/settings-dialog/settings-dialog.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ProjectExplorerComponent,
    HistoryPanelComponent,
    MonacoEditorComponent,
    TaskPanelComponent,
    DiagnosticsDrawerComponent,
    SettingsDialogComponent,
  ],
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.css'],
})
export class AppComponent implements OnInit {
  public showHistory: boolean = false;
  public showProviders: boolean = false;
  public showSettings: boolean = false;

  constructor(public state: StateService) {}

  ngOnInit(): void {
    // Load initial project status from local server
    this.state.loadProject();
  }

  public selectTab(tab: any): void {
    this.state.activeTab.set(tab);
    this.state.activeMainView.set('editor');
  }

  public closeTab(tabId: string, event: MouseEvent): void {
    event.stopPropagation();
    this.state.closeTab(tabId);
  }
}
