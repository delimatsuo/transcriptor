using TarsCompanionCLI;

public class ProgramModeTests
{
    [Fact]
    public void NonSimulateModeIsRefusedOnNonWindows() => Assert.Equal(2, CaptureModeGate.Validate(simulate: false, isWindows: false));

    [Fact]
    public void LiveModeIsAllowedOnWindows() => Assert.Equal(0, CaptureModeGate.Validate(simulate: false, isWindows: true));

    [Fact]
    public void SimulateModeIsAllowedOnAnyPlatform()
    {
        Assert.Equal(0, CaptureModeGate.Validate(simulate: true, isWindows: false));
        Assert.Equal(0, CaptureModeGate.Validate(simulate: true, isWindows: true));
    }
}
