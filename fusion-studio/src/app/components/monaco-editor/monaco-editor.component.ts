import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { StateService } from '../../services/state.service';

declare const window: any;
declare const require: any;

@Component({
  selector: 'app-monaco-editor',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="editor-wrapper">
      <div #editorContainer class="editor-container"></div>
    </div>
  `,
  styles: [
    `
      .editor-wrapper {
        width: 100%;
        height: 100%;
        position: relative;
        background-color: #1e1e1e;
        overflow: hidden;
      }
      .editor-container {
        width: 100%;
        height: 100%;
      }
    `,
  ],
})
export class MonacoEditorComponent implements AfterViewInit, OnChanges, OnDestroy {
  @ViewChild('editorContainer', { static: true }) editorContainer!: ElementRef<HTMLDivElement>;

  @Input() mode: 'code' | 'diff' = 'code';
  @Input() content: string = '';
  @Input() originalContent: string = '';
  @Input() modifiedContent: string = '';
  @Input() language: string = 'python';
  @Input() readOnly: boolean = false;

  private editor: any = null;
  private diffEditor: any = null;
  private monacoInstance: any = null;

  constructor(private state: StateService) {}

  ngAfterViewInit(): void {
    this.ensureMonacoLoaded().then((monaco) => {
      this.monacoInstance = monaco;
      this.initEditor();
    });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.editor && changes['content'] && !changes['content'].isFirstChange()) {
      const curVal = this.editor.getValue();
      if (curVal !== this.content) {
        this.editor.setValue(this.content || '');
      }
    }

    if (this.editor && changes['language'] && this.monacoInstance) {
      const model = this.editor.getModel();
      if (model) {
        this.monacoInstance.editor.setModelLanguage(model, this.language);
      }
    }

    if (this.diffEditor && (changes['originalContent'] || changes['modifiedContent'])) {
      this.updateDiffModels();
    }

    if (changes['mode'] && !changes['mode'].isFirstChange()) {
      this.destroyEditor();
      this.initEditor();
    }
  }

  private ensureMonacoLoaded(): Promise<any> {
    return new Promise((resolve) => {
      const win = typeof window !== 'undefined' ? (window as any) : {};
      if (typeof win.monaco !== 'undefined') {
        resolve(win.monaco);
        return;
      }
      if (typeof win['require'] !== 'undefined' && win['require'].config) {
        win['require'].config({ paths: { vs: 'assets/monaco/vs' } });
        win['require'](['vs/editor/editor.main'], () => {
          resolve(win.monaco);
        });
      } else {
        const interval = setInterval(() => {
          if (typeof win.monaco !== 'undefined') {
            clearInterval(interval);
            resolve(win.monaco);
          }
        }, 100);
      }
    });
  }

  private initEditor(): void {
    if (!this.editorContainer || !this.monacoInstance) return;

    if (this.mode === 'code') {
      this.editor = this.monacoInstance.editor.create(this.editorContainer.nativeElement, {
        value: this.content || '',
        language: this.language || 'python',
        theme: 'vs-dark',
        readOnly: this.readOnly,
        automaticLayout: true,
        fontSize: 13,
        lineNumbers: 'on',
        minimap: { enabled: true, maxColumn: 80 },
        scrollBeyondLastLine: false,
        fontFamily: "'Fira Code', 'Cascadia Code', Consolas, monospace",
      });

      this.editor.onDidChangeModelContent(() => {
        const val = this.editor?.getValue() || '';
        this.state.updateActiveTabContent(val);
      });

      // Keyboard shortcut Ctrl+S / Cmd+S
      this.editor.addCommand(this.monacoInstance.KeyMod.CtrlCmd | this.monacoInstance.KeyCode.KeyS, () => {
        this.state.saveActiveTab();
      });
    } else {
      this.diffEditor = this.monacoInstance.editor.createDiffEditor(this.editorContainer.nativeElement, {
        theme: 'vs-dark',
        readOnly: true,
        automaticLayout: true,
        fontSize: 13,
        fontFamily: "'Fira Code', 'Cascadia Code', Consolas, monospace",
        renderSideBySide: true,
      });

      this.updateDiffModels();
    }
  }

  private updateDiffModels(): void {
    if (!this.diffEditor || !this.monacoInstance) return;

    const originalModel = this.monacoInstance.editor.createModel(this.originalContent || '', this.language || 'python');
    const modifiedModel = this.monacoInstance.editor.createModel(this.modifiedContent || '', this.language || 'python');

    this.diffEditor.setModel({
      original: originalModel,
      modified: modifiedModel,
    });
  }

  private destroyEditor(): void {
    if (this.editor) {
      this.editor.dispose();
      this.editor = null;
    }
    if (this.diffEditor) {
      this.diffEditor.dispose();
      this.diffEditor = null;
    }
  }

  ngOnDestroy(): void {
    this.destroyEditor();
  }
}
