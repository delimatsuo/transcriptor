using TarsCompanionCLI;

public class ProgramModeTests
{
    [Fact]
    public void NonSimulateModeIsRefused() => Assert.Equal(2, CaptureModeGate.Validate(simulate: false));

    [Fact]
    public void SimulateModeIsAllowed() => Assert.Equal(0, CaptureModeGate.Validate(simulate: true));
}
