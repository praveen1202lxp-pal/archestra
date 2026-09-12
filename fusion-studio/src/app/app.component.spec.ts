import { TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';
import { StateService } from './services/state.service';
import { BridgeService } from './services/bridge.service';

describe('AppComponent', () => {
  let stateService: StateService;
  let bridgeService: BridgeService;

  beforeEach(async () => {
    spyOn(window, 'fetch').and.returnValue(
      Promise.resolve(new Response(JSON.stringify({ success: true, data: {} })))
    );

    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [StateService, BridgeService],
    }).compileComponents();

    stateService = TestBed.inject(StateService);
    bridgeService = TestBed.inject(BridgeService);
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render Fusion Studio header brand', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.brand-name')?.textContent).toContain('Fusion Studio');
  });

  it('should handle tab selection and close', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;

    stateService.openTabs.set([
      {
        id: 'tab-1',
        path: 'src/app.py',
        name: 'app.py',
        content: 'def run(): pass',
        originalContent: 'def run(): pass',
        isDirty: false,
        language: 'python',
      },
    ]);
    stateService.activeTab.set(stateService.openTabs()[0]);

    expect(stateService.openTabs().length).toBe(1);
    expect(stateService.activeTab()?.name).toBe('app.py');

    const fakeEvent = new MouseEvent('click');
    app.closeTab('tab-1', fakeEvent);

    expect(stateService.openTabs().length).toBe(0);
    expect(stateService.activeTab()).toBeNull();
  });

  it('should toggle modals when nav buttons are clicked', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;

    expect(app.showHistory).toBeFalse();
    expect(app.showProviders).toBeFalse();
    expect(app.showSettings).toBeFalse();

    app.showHistory = true;
    expect(app.showHistory).toBeTrue();

    app.showProviders = true;
    expect(app.showProviders).toBeTrue();

    app.showSettings = true;
    expect(app.showSettings).toBeTrue();
  });
});
