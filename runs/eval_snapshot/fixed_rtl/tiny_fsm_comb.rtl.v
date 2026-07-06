// tiny_fsm_comb: two-state FSM with combinational next-state logic.
// BUG: state transition condition is wrong -> FSM stuck in S0 forever.
module tiny_fsm_comb (
    input  wire clk,
    input  wire rst,
    input  wire in,
    output reg  out
);
    localparam S0 = 1'b0;
    localparam S1 = 1'b1;

    reg state;
    reg next;

    // state register
    always @(posedge clk or posedge rst) begin
        if (rst)
            state <= S0;
        else
            state <= next;
    end

    // next-state logic (combinational) -- BUGGY
    always @(*) begin
        case (state)
            S0: next = S0;          // BUG: should be next = in ? S1 : S0;
            S1: next = in ? S0 : S1;
            default: next = S0;
        endcase
    end

    // Moore output
    always @(*) begin
        out = state;
    end
endmodule
