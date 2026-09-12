import { TestBed } from '@angular/core/testing';
import { StateService } from './state.service';
import { BridgeService } from './bridge.service';

describe('StateService', () => {
  let service: StateService;
  let bridgeService: BridgeService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [StateService, BridgeService],
    });
    service = TestBed.inject(StateService);
    bridgeService = TestBed.inject(BridgeService);
  });

  it('should be created with default project state', () => {
    expect(service).toBeTruthy();
    expect(service.project().name).toBe('Fusion Studio');
    expect(service.project().version).toBe('0.12.0');
    expect(service.openTabs().length).toBe(0);
  });

  it('should open new tab and set as active', async () => {
    await service.openFile('src/app.py');
    expect(service.openTabs().length).toBe(1);
    expect(service.activeTab()?.name).toBe('app.py');
    expect(service.activeTab()?.language).toBe('python');
    expect(service.activeTab()?.isDirty).toBeFalse();
  });

  it('should mark tab dirty when content changes', async () => {
    await service.openFile('src/app.py');
    service.updateActiveTabContent('new modified content');
    expect(service.activeTab()?.isDirty).toBeTrue();
  });

  it('should close tab and reset active tab', async () => {
    await service.openFile('src/app.py');
    const tabId = service.openTabs()[0].id;
    service.closeTab(tabId);
    expect(service.openTabs().length).toBe(0);
    expect(service.activeTab()).toBeNull();
  });

  it('should update task state on submitTask', async () => {
    await service.submitTask('Add feature X');
    expect(service.activeTask().instruction).toBe('Add feature X');
    expect(service.activeTask().timeline.length).toBeGreaterThan(0);
  });
});
